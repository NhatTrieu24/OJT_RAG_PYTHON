import os
import re
import uvicorn
import vertexai
import psycopg2
import requests
import pdfplumber
import docx
import time
from urllib.parse import unquote
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage 
from apscheduler.schedulers.background import BackgroundScheduler
import fitz
# Import logic từ agent_adk
from agent_adk import run_agent, run_cv_review, get_query_embedding, sync_missing_embeddings
from file_parser import extract_text_from_file

# ==================== CẤU HÌNH ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "us-west1" 
DB_DSN = "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway"

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
            # Mở file bằng PyMuPDF
            with fitz.open(file_path) as doc:
                for page in doc:
                    # Trích xuất văn bản theo khối để giữ cấu trúc tốt hơn
                    text += page.get_text("text") + "\n"
                    
        elif file_path.endswith(".docx"):
            import docx
            doc = docx.Document(file_path)
            for p in doc.paragraphs:
                text += p.text + "\n"
                
    except Exception as e:
        print(f"❌ Lỗi trích xuất văn bản: {e}")
        # Nếu lỗi nặng, trả về chuỗi rỗng để không làm hỏng logic phía sau
        return ""
    
    return text

# ==================== SCHEDULED TASK ====================
def start_scheduler():
    """Khởi tạo trình lập lịch chạy ngầm mỗi 2 giờ"""
    scheduler = BackgroundScheduler()
    # Thêm công việc chạy hàm sync mỗi 2 giờ
    scheduler.add_job(sync_missing_embeddings, 'interval', hours=2)
    scheduler.start()
    print("⏰ [Scheduler] Đã kích hoạt tự động đồng bộ mỗi 2 giờ.")

# ==================== LIFESPAN & APP ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        # Khởi tạo Vertex AI
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        print("✅ Vertex AI initialized!")
        
        # 1. Chạy sync ngay lập tức khi khởi động
        print("🔄 [Startup] Đang quét dữ liệu mới từ DB...")
        sync_missing_embeddings() 
        
        # 2. Bắt đầu trình lập lịch định kỳ
        start_scheduler()
        
    except Exception as e:
        print(f"❌ Startup Error: {e}")
    yield

app = FastAPI(title="OJT RAG (Vector + AutoSync) V4", version="V2.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ==================== API 1: CHAT ====================
@app.post("/chat")
async def chat_endpoint(question: str = Form(...), file: UploadFile = File(None)):
    try:
        if file:
            cv_text = await extract_text_from_file(file, file.filename)
            if cv_text.startswith("Lỗi"): return {"answer": "Lỗi đọc CV.", "sql_debug": "Error"}
            answer, debug = run_cv_review(cv_text, question)
            return {"answer": answer, "sql_debug": debug}
        else:
            answer, debug = run_agent(question)
            return {"answer": answer, "sql_debug": debug}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== API 2: IMPORT ====================
@app.post("/import_pdf")
async def import_pdf(url: str = Query(...)):
    temp_file = "temp_import.pdf"
    conn = None
    try:
        real_filename = "Imported_Doc.pdf"
        if "drive.google.com" in url:
            success, fname = download_drive_file(url, temp_file)
            if not success: return {"message": "Lỗi tải Google Drive."}
            real_filename = fname 
        elif url.startswith("gs://"):
             return {"message": "Hiện tại ưu tiên Drive link."}
        else:
            return {"message": "Link không hỗ trợ."}

        content = extract_text_local(temp_file)
        if not content: return {"message": "File rỗng."}
        
        vector = get_query_embedding(content[:8000])
        
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        sql = "INSERT INTO ojtdocument (title, file_url, embedding) VALUES (%s, %s, %s)"
        cur.execute(sql, (real_filename, url, vector))
        conn.commit()
        
        return {"message": f"✅ Import thành công: {real_filename}", "title": real_filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_file): os.remove(temp_file)
        if conn: conn.close()

# ==================== CÁC API KHÁC ====================
@app.get("/list_files")
async def list_files_endpoint():
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        cur.execute("SELECT ojtdocument_id, title, file_url FROM ojtdocument ORDER BY ojtdocument_id DESC")
        rows = cur.fetchall()
        files = [{"id": r[0], "display_name": r[1], "gcs_uri": r[2]} for r in rows]
        return {"files": files}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.delete("/delete_file")
async def delete_file(resource_name: str = Query(...)):
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        if resource_name.isdigit(): cur.execute("DELETE FROM ojtdocument WHERE ojtdocument_id = %s", (resource_name,))
        else: cur.execute("DELETE FROM ojtdocument WHERE title = %s", (resource_name,))
        conn.commit()
        return {"message": f"Đã xóa: {resource_name}"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.get("/status")
async def status():
    return {
        "status": "LIVE", 
        "mode": "Vector + AutoSync + Scheduler Active",
        "next_sync_check": "Every 2 hours"
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
