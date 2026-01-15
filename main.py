# main.py – FINAL CLEAN & FIXED (Vertex AI Pager + Lifespan + Error Handling)
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from google.cloud import storage

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

@app.middleware("http")
async def debug_origin(request, call_next): 
    print("METHOD:", request.method)
    print("ORIGIN:", request.headers.get("origin"))
    return await call_next(request)

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
            # Khởi tạo gcs_uri mặc định là N/A
            gcs_uri = "N/A"
            
            # Kiểm tra cấu trúc file_spec.gcs_source.uri (Cách Vertex AI RAG lưu)
            if hasattr(f, 'file_spec') and f.file_spec.gcs_source:
                gcs_uri = f.file_spec.gcs_source.uri
            
            result.append({
                "display_name": f.display_name,
                "gcs_uri": gcs_uri,
                "resource_name": f.name # Đây là cái projects/.../ragFiles/...
            })
        return {"files": result}
    except Exception as e:
        # Nếu vẫn lỗi, in toàn bộ đối tượng ra console để debug
        if files and len(files) > 0:
            print(f"DEBUG - Cấu trúc file mẫu: {files[0]}")
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
async def delete_file(
    gcs_uri: Optional[str] = Query(None), 
    resource_name: Optional[str] = Query(None)
):
    """
    Xóa file: Ưu tiên xóa theo resource_name nếu có, 
    nếu không sẽ tìm theo gcs_uri.
    """
    try:
        target_name = None

        # 1. Tìm ID của file trong Vertex AI
        if resource_name:
            target_name = resource_name
        elif gcs_uri:
            files = get_files_list()
            target = next((f for f in files if gcs_uri in str(f)), None)
            if target:
                target_name = target.name
        
        if not target_name:
            return {"error": "Không tìm thấy file để xóa. Vui lòng cung cấp resource_name chính xác."}

        # 2. Xóa khỏi Vertex AI Corpus
        rag.delete_file(name=target_name)
        
        # 3. Xóa file vật lý trên GCS (Nếu bạn có gcs_uri)
        if gcs_uri and gcs_uri.startswith("gs://"):
            try:
                path_parts = gcs_uri.replace("gs://", "").split("/", 1)
                storage_client = storage.Client()
                storage_client.bucket(path_parts[0]).blob(path_parts[1]).delete()
                print(f"Đã xóa GCS: {gcs_uri}")
            except Exception as e_gcs:
                print(f"GCS Delete Skip: {e_gcs}")

        return {"message": f"Đã xóa thành công file: {target_name}"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete error: {str(e)}")

print("Main.py loaded successfully")