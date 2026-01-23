import os
import io
import time
import threading
import requests
import uvicorn
import vertexai
import psycopg2
import fitz  # PyMuPDF (Chuyên trị PDF)
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler
gcp_json_content = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")

if gcp_json_content:
    # Nếu biến này chứa nội dung JSON (bắt đầu bằng {), ta ghi nó ra file
    if gcp_json_content.strip().startswith("{"):
        print("🔑 [Auth] Phát hiện JSON Content từ Env Var. Đang tạo file tạm...")
        cred_path = "google_creds.json"
        with open(cred_path, "w") as f:
            f.write(gcp_json_content)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(cred_path)
    # Nếu nó là đường dẫn file
    else:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = gcp_json_content
# Import logic từ agent_adk
from agent_adk import run_agent, run_cv_review, sync_all_data,SYNC_STATE

# ==================== CẤU HÌNH HỆ THỐNG ====================
PROJECT_ID = os.environ.get("PROJECT_ID", "reflecting-surf-477600-p4")
LOCATION = os.environ.get("LOCATION", "us-central1")
DB_DSN = os.environ.get("DB_DSN", "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway")

# Key Google Cloud
render_secret = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"
local_key = "rag-service-account.json" 

if os.path.exists(render_secret): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = render_secret
elif os.path.exists(local_key): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(local_key)

sync_status = {
    "is_running": False,
    "current_step": "Sẵn sàng",
    "progress": "0/0",
    "percentage": "0%",
    "last_finished": None
}

# ==================== LIFESPAN ====================
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_all_data, 'interval', hours=2, args=[False])
    scheduler.start()
    print("⏰ [Scheduler] Đã kích hoạt tự động đồng bộ mỗi 2 giờ.")

def keep_alive():
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL") 
    if not RENDER_URL: return
    time.sleep(30)
    while True:
        try:
            requests.get(RENDER_URL, timeout=10)
            print(f"⚓ [Keep-Alive] Ping {RENDER_URL}")
        except: pass
        time.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        print(f"✅ [Startup] Vertex AI initialized ({LOCATION})")
    except Exception as e:
        print(f"⚠️ [Startup] Vertex AI Warning: {e}")

    start_scheduler()
    if os.environ.get("RENDER"):
        threading.Thread(target=keep_alive, daemon=True).start()

    yield
    print("👋 [Shutdown] Server stopping...")

# ==================== APP INITIALIZATION ====================
app = FastAPI(title="OJT RAG V8", version="4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== API ENDPOINTS ====================

@app.get("/")
async def root():
    return {"status": "Live", "mode": "PDF Only", "db": "Connected"}

@app.post("/chat")
async def chat_endpoint(
    question: str = Form(...), 
    file: UploadFile = File(None)
):
    try:
        file_content = ""
        has_valid_file = False

        # --- BƯỚC 1: KIỂM TRA FILE (NẾU CÓ) ---
        if file and file.filename:
            # 1.1 CHECK ĐUÔI FILE (BẮT BUỘC PDF)
            if not file.filename.lower().endswith(".pdf"):
                print(f"⚠️ [Upload] Từ chối file: {file.filename} (Không phải PDF)")
                return {
                    "answer": "❌ Hệ thống chỉ hỗ trợ định dạng PDF. Vui lòng tải lên file .pdf để được phân tích.",
                    "active_model": "File Error",
                    "sql_debug": "Invalid Format"
                }

            # 1.2 ĐỌC NỘI DUNG PDF
            content_bytes = await file.read()
            
            # Check file rỗng (0 bytes)
            if len(content_bytes) > 0:
                print(f"📂 [Upload] Đang đọc PDF: {file.filename} ({len(content_bytes)} bytes)")
                try:
                    # Dùng PyMuPDF (Fitz) để đọc siêu nhanh
                    with fitz.open(stream=content_bytes, filetype="pdf") as doc:
                        file_content = "\n".join([page.get_text() for page in doc])
                        has_valid_file = True
                except Exception as e:
                    print(f"⚠️ Lỗi đọc PDF: {e}")
                    return {
                        "answer": "❌ File PDF bị lỗi hoặc đặt mật khẩu. Vui lòng thử file khác.",
                        "active_model": "PDF Error",
                        "sql_debug": str(e)
                    }
            else:
                print("⚠️ [Upload] File PDF rỗng (0 bytes). Bỏ qua.")

        # --- BƯỚC 2: CHẠY LOGIC ---
        
        # MODE 1: REVIEW CV (Có PDF + Nội dung > 50 ký tự)
        if has_valid_file and len(file_content.strip()) > 50:
            print("🤖 [Mode] CV Review (PDF detected)")
            answer, mode = run_cv_review(file_content, question)
        
        # MODE 2: CHAT THƯỜNG (Không có file hoặc file lỗi)
        else:
            print("🤖 [Mode] RAG Chat (No file)")
            answer, mode = run_agent(question, file_content=None)

        return {
            "answer": answer, 
            "active_model": mode, 
            "sql_debug": mode
        }

    except Exception as e:
        print(f"❌ Server Error: {e}")
        return JSONResponse(
            content={"answer": "Lỗi xử lý server.", "error": str(e)}, 
            status_code=500
        )

# ==================== SYNC WORKER ====================
def sync_worker(force_reset: bool):
    global sync_status
    sync_status["is_running"] = True
    sync_status["current_step"] = "Đang đồng bộ..."
    try:
        sync_all_data(force_reset)
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        cur.execute("""
            SELECT SUM(cnt), SUM(idx) FROM (
                SELECT COUNT(*) as cnt, COUNT(embedding) as idx FROM ojtdocument
                UNION ALL SELECT COUNT(*), COUNT(embedding) FROM job_position
                UNION ALL SELECT COUNT(*), COUNT(embedding) FROM company
            ) as s
        """)
        total, indexed = cur.fetchone()
        sync_status["progress"] = f"{indexed or 0}/{total or 0}"
        sync_status["percentage"] = f"{(indexed/(total or 1))*100:.1f}%"
        sync_status["current_step"] = "Hoàn tất"
        conn.close()
    except Exception as e:
        sync_status["current_step"] = f"Lỗi: {e}"
    finally:
        sync_status["is_running"] = False
        sync_status["last_finished"] = time.strftime("%H:%M:%S %d/%m/%Y")

@app.get("/SyncNow")
async def sync_now(background_tasks: BackgroundTasks, force: bool = False):
    if sync_status["is_running"]: return {"message": "Busy"}
    background_tasks.add_task(sync_worker, force)
    return {"message": "Started"}

@app.get("/SyncStatus")
async def get_sync_status():
    """API trả về tiến độ Real-time cho Frontend"""
    
    # 1. Lấy thông tin Text (Đang làm gì) từ agent_adk
    response = {
        "is_running": SYNC_STATE["is_running"],
        "step": SYNC_STATE["step"],       # VD: "Đang xử lý ojtdocument"
        "detail": SYNC_STATE["detail"],   # VD: "Đang đọc file: Report.pdf..."
        "progress_text": "0/0",
        "percentage": 0
    }

    # 2. Lấy con số thống kê thực tế từ DB (Để vẽ thanh % chính xác)
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        # Đếm tổng số dòng đã Index vs Tổng số dòng
        cur.execute("""
            SELECT SUM(idx), SUM(cnt) FROM (
                SELECT COUNT(embedding) as idx, COUNT(*) as cnt FROM ojtdocument
                UNION ALL SELECT COUNT(embedding), COUNT(*) FROM job_position
                UNION ALL SELECT COUNT(embedding), COUNT(*) FROM company
                UNION ALL SELECT COUNT(embedding), COUNT(*) FROM "User"
            ) as s
        """)
        indexed, total = cur.fetchone()
        conn.close()

        total = total if total else 1
        indexed = indexed if indexed else 0
        
        response["progress_text"] = f"{indexed}/{total}"
        response["percentage"] = round((indexed / total) * 100, 1)

    except Exception:
        # Nếu lỗi kết nối DB thì trả về số liệu tạm
        response["progress_text"] = "Checking..."
    
    return response

@app.get("/list_files")
async def list_files():
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        cur.execute('SELECT ojtdocument_id, title, file_url FROM ojtdocument ORDER BY ojtdocument_id DESC LIMIT 50')
        rows = cur.fetchall()
        conn.close()
        return {"files": [{"id": r[0], "display_name": r[1], "url": r[2]} for r in rows]}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
