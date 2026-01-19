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

if os.path.exists(render_secret): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = render_secret
elif os.path.exists(local_key): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(local_key)

# ==================== HELPER FUNCTIONS ====================

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
        
        # Bắt đầu bộ lập lịch chạy ngầm
        start_scheduler()
    except Exception as e:
        print(f"❌ Startup Error: {e}")
    
    yield
    print("👋 Server is shutting down...")

# ==================== APP INITIALIZATION ====================
app = FastAPI(title="OJT RAG Bot V7.3", version="2.1", lifespan=lifespan)

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
        if file:
            # Xử lý CV tải lên (Sử dụng hàm từ file_parser.py nếu bạn có)
            # Ở đây giả định bạn trích xuất trực tiếp
            content = await file.read()
            # Tạm thời dùng fitz để đọc nội dung file tải lên trực tiếp
            pdf_stream = io.BytesIO(content)
            cv_text = ""
            with fitz.open(stream=pdf_stream, filetype="pdf") as doc:
                cv_text = " ".join([page.get_text() for page in doc])
            
            answer, debug = run_cv_review(cv_text, question)
            return {"answer": answer, "sql_debug": debug}
        else:
            # Trò chuyện bình thường với RAG
            answer, debug = run_agent(question)
            return {"answer": answer, "sql_debug": debug}
    except Exception as e:
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
async def status(background_tasks: BackgroundTasks):
    # Kích hoạt Sync ngay lập tức bằng tay
    background_tasks.add_task(sync_all_data, False)
    return {
        "status": "LIVE", 
        "mode": "Hybrid RAG + AutoSync",
        "sync_trigger": "Manual sync started in background"
    }

# ==================== SERVER ENTRY POINT ====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    # Chạy uvicorn với đối tượng app
    uvicorn.run(app, host="0.0.0.0", port=port)
