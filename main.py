# main.py – FINAL CLEAN (KHÔNG CÒN FILE CŨ, CHỈ DÙNG FILE BẠN ĐÃ THÊM)
import os
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

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

# ==================== VERTEX AI RAG ====================
import vertexai
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, Tool, Part, Content

PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"
DISPLAY_NAME = "ProductDocumentation"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# ==================== CORS ====================
app = FastAPI(title="RAG OJT 2025 – FINAL CLEAN", version="12.0")


# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=[
#         "http://localhost:3000",
#         "http://127.0.0.1:3000",
#         "https://frontend-ojt-544c.vercel.app"
#     ],
#     allow_origin_regex=r"https://frontend-ojt-544c-.*\.vercel\.app",
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False,           
    allow_methods=["*"],
    allow_headers=["*"],
)
# ==================== RAG SETUP (KHÔNG CÒN FILE CŨ) ====================
def get_corpus():
    corpora = rag.list_corpora()
    corpus = next((c for c in corpora if c.display_name == DISPLAY_NAME), None)
    if not corpus:
        print("Tạo corpus mới...")
        corpus = rag.create_corpus(display_name=DISPLAY_NAME)
    return corpus

corpus = get_corpus()

# KHÔNG CÒN import file cũ nữa – chỉ dùng file bạn đã thêm
rag_resource = rag.RagResource(rag_corpus=corpus.name)
retrieval_tool = Tool.from_retrieval(
    retrieval=rag.Retrieval(source=rag.VertexRagStore(rag_resources=[rag_resource]))
)

model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])

# ==================== API ====================
class Question(BaseModel):
    question: str

@app.get("/")
async def root():
    return {"message": "RAG Backend OJT ", "status": "LIVE"}

@app.post("/chat")
async def chat(q: Question):
    try:
        response = model.generate_content(q.question)
        return {"answer": response.text.strip()}
    except Exception as e:
        return {"error": str(e)}

@app.get("/list_files")
async def list_files():
    files = rag.list_files(corpus.name)
    result = []
    for f in files:
        # Lấy tên file thật từ URI (phần cuối cùng sau /)
        full_uri = f.name if f.name.startswith("gs://") else f.name
        file_name = full_uri.split("/")[-1]
        display_name = file_name if file_name.endswith((".pdf", ".docx", ".txt")) else f"File {file_name[:15]}..."
        result.append(display_name)
    return {"files": result}

@app.post("/import_pdf")
async def import_pdf(gcs_uri: str = Query(...)):
    try:
        if any(gcs_uri in str(f) for f in rag.list_files(corpus.name)):
            return {"message": "File đã tồn tại"}
        rag.import_files(corpus.name, paths=[gcs_uri])
        file_name = gcs_uri.split("/")[-1]
        return {"message": f"Import thành công: {file_name}"}
    except Exception as e:
        return {"error": str(e)}

@app.delete("/delete_file")
async def delete_file(gcs_uri: str = Query(...)):
    try:
        target = next((f for f in rag.list_files(corpus.name) if gcs_uri in f.name), None)
        if target:
            rag.delete_file(name=target.name)
            return {"message": f"Đã xóa {gcs_uri.split('/')[-1]}"}
        return {"error": "Không tìm thấy file"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/status")
async def status():
    files = rag.list_files(corpus.name)
    return {
        "status": "HOÀN HẢO",
        "model": "gemini-2.5-pro",
        "corpus": DISPLAY_NAME,
        "total_files": len(files),
        "files": [f.name.split("/")[-1] for f in files]
    }

@app.get("/health")
async def health():
    return {"status": "ok"}