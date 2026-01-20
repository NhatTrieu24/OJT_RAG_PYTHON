import os
import re
import uvicorn
import vertexai
import psycopg2
import requests
import io
import time
import threading
from urllib.parse import unquote
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
import fitz  # PyMuPDF

# Import logic từ agent_adk
from agent_adk import run_agent, run_cv_review, get_query_embedding, sync_all_data

# ==================== CẤU HÌNH ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "us-west1" 
DB_DSN = "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway"

# Cấu hình đường dẫn Service Account
render_secret = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"
local_key = "rag-service-account.json" 
# Biến lưu trạng thái đồng bộ
sync_status = {
    "is_running": False,
    "current_step": "Chưa bắt đầu",
    "progress": "0/0",
    "percentage": "0%",
    "last_finished": None
}
if os.path.exists(render_secret): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = render_secret
elif os.path.exists(local_key): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(local_key)

# ==================== HELPER FUNCTIONS ====================
    
def keep_alive():
    """Hàm tự gửi request đến chính mình (Chỉ chạy trên Render)"""
    # Lấy URL từ biến môi trường hoặc cấu hình Render của bạn
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL") 
    
    if not RENDER_URL:
        print("🏠 [Keep-Alive] Đang chạy Local, bỏ qua cơ chế chống ngủ.")
        return

    time.sleep(30)
    while True:
        try:
            requests.get(RENDER_URL, timeout=10)
            print(f"⚓ [Keep-Alive] Đã gửi Ping đến {RENDER_URL}")
        except Exception as e:
            print(f"⚠️ [Keep-Alive] Ping failed: {e}")
        
        time.sleep(600) # 10 phút

def get_filename_from_cd(cd):
    if not cd: return None
    fname_match = re.search(r"filename\*=UTF-8''(.+)", cd)
    if fname_match: return unquote(fname_match.group(1))
    fname_match = re.search(r'filename="?([^"]+)"?', cd)
    if fname_match:
        filename = fname_match.group(1)
        try: return filename.encode('iso-8859-1').decode('utf-8')
        except: return filename
    return None

def download_drive_file(drive_url, destination_path):
    try:
        file_id = None
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", drive_url)
        if match: file_id = match.group(1)
        else:
            match = re.search(r"id=([a-zA-Z0-9_-]+)", drive_url)
            if match: file_id = match.group(1)
            
        if not file_id: return False, "Unknown.pdf"

        url = f"https://drive.google.com/uc?id={file_id}&export=download"
        print(f"⬇️ Downloading Drive ID: {file_id}...")
        
        response = requests.get(url, stream=True)
        filename = "Google_Drive_Doc.pdf"
        if "Content-Disposition" in response.headers:
            detected_name = get_filename_from_cd(response.headers["Content-Disposition"])
            if detected_name: filename = detected_name
                
        filename = re.sub(r'[\\/*?:"<>|]', "", filename)

        with open(destination_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: f.write(chunk)
                
        print(f"✅ Saved as: {filename}")
        return True, filename
    except Exception as e:
        print(f"❌ Drive Error: {e}")
        return False, None

def extract_text_local(file_path):
    text = ""
    try:
        if file_path.endswith(".pdf"):
            with fitz.open(file_path) as doc:
                for page in doc:
                    text += page.get_text("text") + "\n"
        elif file_path.endswith(".docx"):
            import docx
            doc = docx.Document(file_path)
            for p in doc.paragraphs:
                text += p.text + "\n"
    except Exception as e:
        print(f"❌ Lỗi trích xuất văn bản: {e}")
        return ""
    return text

# ==================== SCHEDULED TASK ====================
def start_scheduler():
    scheduler = BackgroundScheduler()
    # Tự động cập nhật các thay đổi mới mỗi 2 giờ (Smart Update)
    scheduler.add_job(
        sync_all_data, 
        'interval', 
        hours=2, 
        args=[False] 
    )
    scheduler.start()
    print("⏰ [Scheduler] Đã kích hoạt tự động đồng bộ mỗi 2 giờ.")

# ==================== LIFESPAN ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo Vertex AI khi server bắt đầu
    try:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        print("✅ Vertex AI initialized!")
        
        # CHẠY SYNC TRONG THREAD RIÊNG: Quan trọng để Render không bị Timeout Port
        # Để force_reset=False để tối ưu tốc độ startup
        sync_thread = threading.Thread(target=sync_all_data, args=(False,))
        sync_thread.start()

        # Chỉ chạy Keep-Alive nếu đang ở trên Render
        if os.environ.get("RENDER"): 
            threading.Thread(target=keep_alive, daemon=True).start()
        else:
            print("💻 [Local Mode] Tự động tắt tính năng Keep-Alive.")

        # Bắt đầu bộ lập lịch chạy ngầm
        start_scheduler()
    
    except Exception as e:
        print(f"❌ Startup Error: {e}")
    
    yield
    print("👋 Server is shutting down...")

# ==================== APP INITIALIZATION ====================
app = FastAPI(title="OJT RAG Bot V7.4", version="2.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ==================== API ENDPOINTS ====================

@app.get("/")
async def root():
    return {
        "message": "OJT RAG System is Live",
        "region": LOCATION,
        "database": "Connected",
        "docs": "/docs"
    }

@app.post("/chat")
async def chat_endpoint(question: str = Form(...), file: UploadFile = File(None)):
    try:
        # TRƯỜNG HỢP 1: CÓ FILE GỬI KÈM (Lần đầu hỏi hoặc muốn phân tích file mới)
        if file:
            print(f"📄 [CV Mode] Đang xử lý: {file.filename}")
            content = await file.read()
            pdf_stream = io.BytesIO(content)
            
            # Trích xuất văn bản từ file tải lên
            cv_text = ""
            with fitz.open(stream=pdf_stream, filetype="pdf") as doc:
                cv_text = " ".join([page.get_text() for page in doc])
            
            # Sử dụng model CV Analysis
            answer, debug = run_cv_review(cv_text, question)
            
            return {
                "answer": answer, 
                "sql_debug": debug, 
                "active_model": "CV Analysis Mode"
            }
        
        # TRƯỜNG HỢP 2: KHÔNG GỬI FILE (Lần 2 hoặc các lần hỏi bình thường)
        else:
            print("🤖 [RAG Mode] Đang sử dụng dữ liệu hệ thống.")
            # Sử dụng model RAG mặc định (truy vấn Database)
            answer, debug = run_agent(question)
            
            return {
                "answer": answer, 
                "sql_debug": debug, 
                "active_model": "RAG Mode"
            }

    except Exception as e:
        print(f"❌ Lỗi Chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/list_files")
async def list_files_endpoint():
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        cur.execute('SELECT ojtdocument_id, title, file_url FROM ojtdocument ORDER BY ojtdocument_id DESC')
        rows = cur.fetchall()
        files = [{"id": r[0], "display_name": r[1], "gcs_uri": r[2]} for r in rows]
        return {"files": files}
    except Exception as e: 
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/status")
async def status():
    return {
        "status": "LIVE", 
        "mode": "Hybrid RAG + AutoSync",
        "sync_trigger": "Manual sync started in background"
    }

def sync_worker(force_reset: bool):
    global sync_status
    sync_status["is_running"] = True
    sync_status["current_step"] = "Đang xóa bộ nhớ cũ và quét toàn bộ các bảng..."
    
    try:
        # 1. Gọi hàm sync gốc (Reset hoặc Smart Update)
        sync_all_data(force_reset)
        
        # 2. Kiểm tra kết quả tổng hợp từ tất cả các bảng
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # Truy vấn gộp để tính tổng dòng và tổng embedding của 3 bảng chính
        query = """
            SELECT SUM(total_count), SUM(indexed_count)
            FROM (
                SELECT COUNT(*) as total_count, COUNT(embedding) as indexed_count FROM ojtdocument
                UNION ALL
                SELECT COUNT(*), COUNT(embedding) FROM job_position
                UNION ALL
                SELECT COUNT(*), COUNT(embedding) FROM company
            ) as combined_stats
        """
        cur.execute(query)
        total, indexed = cur.fetchone()
        
        # Đảm bảo không bị lỗi chia cho 0 nếu DB trống
        total = total if total else 0
        indexed = indexed if indexed else 0
        
        cur.close()
        conn.close()

        sync_status["progress"] = f"{indexed}/{total}"
        sync_status["percentage"] = f"{(indexed/total)*100 if total > 0 else 0:.1f}%"
        sync_status["current_step"] = "Hoàn tất đồng bộ toàn bộ hệ thống!"
        
    except Exception as e:
        sync_status["current_step"] = f"Lỗi: {str(e)}"
    finally:
        sync_status["is_running"] = False
        sync_status["last_finished"] = time.strftime("%H:%M:%S %d/%m/%Y")
@app.get("/SyncNow")
async def sync_now_endpoint(background_tasks: BackgroundTasks):
    if sync_status["is_running"]:
        return {"message": "Đang có tiến trình chạy ngầm, vui lòng đợi."}
    
    background_tasks.add_task(sync_worker, True)
    return {"message": "Đã bắt đầu Reset và Sync dữ liệu..."}

@app.get("/SyncStatus")
async def get_sync_status():
    global sync_status
    
    # Nếu đang chạy, ta cập nhật con số mới nhất từ DB mỗi khi API được gọi
    if sync_status["is_running"]:
        try:
            conn = psycopg2.connect(dsn=DB_DSN)
            cur = conn.cursor()
            # Query tương tự như trên để lấy dữ liệu thực tế đang được commit vào DB
            cur.execute("""
                SELECT SUM(t), SUM(i) FROM (
                    SELECT COUNT(*) as t, COUNT(embedding) as i FROM ojtdocument
                    UNION ALL
                    SELECT COUNT(*), COUNT(embedding) FROM job_position
                    UNION ALL
                    SELECT COUNT(*), COUNT(embedding) FROM company
                ) as s
            """)
            total, indexed = cur.fetchone()
            sync_status["progress"] = f"{indexed if indexed else 0}/{total if total else 0}"
            sync_status["percentage"] = f"{(indexed/total)*100 if total and total > 0 else 0:.1f}%"
            cur.close()
            conn.close()
        except:
            pass 

    return sync_status
# ==================== SERVER ENTRY POINT ====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    # Chạy uvicorn với đối tượng app
    uvicorn.run(app, host="0.0.0.0", port=port)
