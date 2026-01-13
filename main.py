# main.py – FINAL CLEAN & FIXED (Vertex AI Pager + Lifespan + Error Handling)
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

# ==================== CREDENTIALS ====================
secret_path = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"
if os.path.exists(secret_path) and os.path.getsize(secret_path) > 0:
    print("Render detect secret file → dùng secret từ Render")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = secret_path
else:
    local_path = os.path.expanduser("~/rag-service-account.json")
    if os.path.exists(local_path):
        print("Local → dùng file key ở home")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = local_path
    else:
        raise FileNotFoundError("Thiếu GCP_SERVICE_ACCOUNT_JSON secret!")

# ==================== VERTEX AI IMPORTS ====================
import vertexai
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, Tool

PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"
DISPLAY_NAME = "ProductDocumentation"

# Biến global để lưu corpus và model (sẽ được set trong lifespan)
corpus = None
model = None

# ==================== LIFESPAN (Startup/Shutdown) ====================
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

# ==================== APP ====================
app = FastAPI(
    title="RAG OJT 2025 – FINAL FIXED",
    version="12.1",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== MODELS ====================
class Question(BaseModel):
    question: str

# ==================== HELPERS ====================
def get_files_list() -> List:
    """Helper: Convert pager thành list files"""
    if corpus is None:
        raise HTTPException(status_code=503, detail="Vertex AI chưa khởi tạo thành công")
    files_pager = rag.list_files(corpus.name)
    return list(files_pager)

# ==================== API ENDPOINTS ====================
@app.get("/")
async def root():
    return {"message": "RAG Backend OJT", "status": "LIVE"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/status")
async def status():
    try:
        files = get_files_list()
        return {
            "status": "HOÀN HẢO",
            "model": "gemini-2.5-pro",
            "corpus": DISPLAY_NAME,
            "total_files": len(files),
            "files": [f.name.split("/")[-1] for f in files]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in /status: {str(e)}")

@app.get("/list_files")
async def list_files():
    try:
        files = get_files_list()
        result = []
        for f in files:
            full_uri = f.name if f.name.startswith("gs://") else f.name
            file_name = full_uri.split("/")[-1]
            display_name = file_name if file_name.endswith((".pdf", ".docx", ".txt")) else f"File {file_name[:15]}..."
            result.append(display_name)
        return {"files": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing files: {str(e)}")

@app.post("/chat")
async def chat(q: Question):
    if model is None:
        raise HTTPException(status_code=503, detail="Model chưa sẵn sàng (Vertex AI init fail)")
    try:
        response = model.generate_content(q.question)
        return {"answer": response.text.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")

@app.post("/import_pdf")
async def import_pdf(gcs_uri: str = Query(...)):
    try:
        files = get_files_list()
        if any(gcs_uri in f.name for f in files):
            return {"message": "File đã tồn tại"}
        
        rag.import_files(corpus.name, paths=[gcs_uri])
        file_name = gcs_uri.split("/")[-1]
        return {"message": f"Import thành công: {file_name}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import error: {str(e)}")

@app.delete("/delete_file")
async def delete_file(gcs_uri: str = Query(...)):
    try:
        files = get_files_list()
        target = next((f for f in files if gcs_uri in f.name), None)
        if target:
            rag.delete_file(name=target.name)
            return {"message": f"Đã xóa {gcs_uri.split('/')[-1]}"}
        return {"error": "Không tìm thấy file"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete error: {str(e)}")

print("Main.py loaded successfully")