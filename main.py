from ast import List
import os
import vertexai
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage
from vertexai.preview import rag  # Dùng cho các tính năng quản lý file cũ

# --- IMPORT MODULE HIỆN TẠI (SQL + PARSER) ---
from agent_adk import run_agent
from file_parser import extract_text_from_file
from vertexai.generative_models import GenerativeModel, Tool
# ==================== 1. CẤU HÌNH & CREDENTIALS ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4" 
DISPLAY_NAME = "OJT_Knowledge_Base" # Tên Corpus lưu trữ

# ==================== CREDENTIALS ====================
# 1. Đường dẫn trên Render (Secret File)
render_secret_path = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"

# 2. Đường dẫn local (Cùng thư mục với main.py)
local_key_file = "rag-service-account.json" 

if os.path.exists(render_secret_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = render_secret_path
    print("--- Chạy trên Deploy: Đã load Secret File ---")

elif os.path.exists(local_key_file):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(local_key_file)
    print(f"--- Chạy Local: Đã load file {local_key_file} ---")

else:
    # Nếu không thấy file nào, kiểm tra xem biến môi trường GOOGLE_APPLICATION_CREDENTIALS đã có chưa
    if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
        raise FileNotFoundError(
            "KHÔNG TÌM THẤY CREDENTIALS! \n"
            "Vui lòng để file 'rag-service-account.json' vào thư mục project."
        )
    print("--- Chạy Local: Sử dụng biến môi trường hệ thống ---")

if os.path.exists(render_secret_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = render_secret_path
    print("--- DEPLOY MODE: Loaded Render Secret ---")
elif os.path.exists(local_key_file):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(local_key_file)
    print(f"--- LOCAL MODE: Loaded {local_key_file} ---")
else:
    print("--- SYSTEM MODE: Using Default Environment Credentials ---")

# Biến toàn cục lưu trữ Corpus
corpus = None
model = None
# ==================== 2. LIFESPAN (KHỞI ĐỘNG HỆ THỐNG) ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global corpus, model
    try:
        print("Initializing Vertex AI...")
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        
        # Load or create corpus
        corpora = rag.list_corpora()
        corpus = next((c for c in corpora if c.display_name == DISPLAY_NAME), None)
        if not corpus:
            print("Tạo corpus mới...")
            corpus = rag.create_corpus(display_name=DISPLAY_NAME)
        
        # Setup retrieval tool
        rag_resource = rag.RagResource(rag_corpus=corpus.name)
        retrieval_tool = Tool.from_retrieval(
            retrieval=rag.Retrieval(source=rag.VertexRagStore(rag_resources=[rag_resource]))
        )
        
        model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])
        print("Vertex AI RAG initialized successfully!")
    except Exception as e:
        print(f"Vertex AI initialization FAILED: {str(e)}")
        # Không raise → server vẫn chạy, endpoint sẽ báo lỗi
    
    yield  # Chạy ứng dụng
    
    # Shutdown (nếu cần cleanup)
    print("Shutting down...")

# ==================== 3. KHỞI TẠO APP ====================
app = FastAPI(
    title="OJT Super Assistant (SQL + RAG + Files)",
    version="2.0 Hybrid",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 4. CÁC API CỐT LÕI (SQL + CV Review) ====================

@app.post("/chat")
async def chat_endpoint(
    question: str = Form(...),
    file: UploadFile = File(None)
):
    """
    API Chính:
    - Nếu có file upload -> Review CV (Dùng logic mới).
    - Nếu không file -> Hỏi đáp Database SQL (Dùng logic agent_adk).
    """
    try:
        file_text = None
        # 1. Xử lý File Upload (RAM)
        if file:
            print(f"📂 Nhận file local: {file.filename}")
            file_text = await extract_text_from_file(file, file.filename)
            if file_text.startswith("Lỗi"):
                return {"answer": file_text, "sql_debug": "N/A"}

        # 2. Gọi Agent xử lý
        print(f"📩 Question: {question}")
        answer, sql = run_agent(question, file_content=file_text)
        
        return {"answer": answer, "sql_debug": sql}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== 5. CÁC API QUẢN TRỊ (Từ Code Cũ) ====================
# Giúp bạn quản lý file trên Vertex AI Corpus (Knowledge Base lâu dài)

def get_files_list() -> List:
    """Helper: Convert pager thành list files"""
    if corpus is None:
        raise HTTPException(status_code=503, detail="Vertex AI chưa khởi tạo thành công")
    files_pager = rag.list_files(corpus.name)
    return list(files_pager)

@app.get("/status")
async def status():
    try:
        files = get_files_list()
        return {
            "status": "LIVE",
            "mode": "Hybrid (SQL Agent + Vertex RAG)",
            "corpus": DISPLAY_NAME,
            "total_indexed_files": len(files),
            "indexed_files": [f.display_name for f in files]
        }
    except Exception as e:
        return {"status": "ERROR", "detail": str(e)}



@app.post("/import_pdf")
async def import_pdf(
    gcs_uri: str = Query(..., description="Nhập link GCS (gs://) hoặc Google Drive")
):
    try:
        # 1. Bảo vệ: Kiểm tra xem RAG Corpus đã sẵn sàng chưa
        if corpus is None:
             raise HTTPException(status_code=503, detail="RAG Corpus chưa được khởi tạo (Do vùng server hoặc lỗi mạng).")

        # 2. Logic cũ: Lấy danh sách file (để in log hoặc check cơ bản)
        files = get_files_list()
        
        # Kiểm tra file đã tồn tại chưa
        if any(gcs_uri in f.name for f in files):
            return {"message": "File đã tồn tại"}
        
        # 3. Gọi lệnh Import (Truyền thẳng link vào, không chặn https)
        print(f"📥 Đang import: {gcs_uri}")
        rag.import_files(corpus.name, paths=[gcs_uri], chunk_size=512)
        
        # 4. Trả kết quả
        # Cắt lấy tên file cuối cùng để hiển thị cho đẹp
        file_name = gcs_uri.split("/")[-1]
        return {"message": f"Import thành công: {file_name}"}

    except Exception as e:
        # In lỗi ra terminal để dễ debug nếu Google từ chối link
        print(f"❌ Lỗi Import: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Import error: {str(e)}")

@app.get("/list_files")
async def list_files_endpoint():
    try:
        files = get_files_list()
        result = []
        for f in files:
            gcs_uri = f.file_spec.gcs_source.uri if (hasattr(f, 'file_spec') and f.file_spec.gcs_source) else "N/A"
            result.append({
                "display_name": f.display_name,
                "gcs_uri": gcs_uri,
                "resource_name": f.name
            })
        return {"files": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.delete("/delete_file")
async def delete_file(
    resource_name: str = Query(..., description="Tên resource (projects/.../ragFiles/...) lấy từ API list_files")
):
    try:
        rag.delete_file(name=resource_name)
        return {"message": f"Đã xóa vĩnh viễn: {resource_name}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)