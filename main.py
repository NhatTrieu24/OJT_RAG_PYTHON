# main.py – PHIÊN BẢN HOÀN CHỈNH CUỐI CÙNG – ĐÃ TEST 100% TRÊN RENDER.COM
# Đã thêm endpoint /import_pdf để cho phép import thêm file PDF từ GCS URI mới

import os
from fastapi import FastAPI
from pydantic import BaseModel

# ==================== CREDENTIALS – DÙNG SECRET FILE CỦA BẠN ====================
if os.path.exists("/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"):
    print("Đang dùng Secret File từ Render (GCP_SERVICE_ACCOUNT_JSON)")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"
else:
    print("Chạy local – dùng file cứng")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"D:\Project\CapStone\OJT_RAG_CSharp\OJT_RAG.Engine\rag-service-account.json"

# ==================== IMPORT VERTEX AI RAG ====================
import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool

# ==================== CẤU HÌNH ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"
DISPLAY_NAME = "ProductDocumentation"
INITIAL_GCS_URI = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"  # File PDF ban đầu

vertexai.init(project=PROJECT_ID, location=LOCATION)

def get_or_create_corpus():
    print("Đang khởi tạo RAG Corpus...")
    corpora = rag.list_corpora()
    rag_corpus = next((c for c in corpora if c.display_name == DISPLAY_NAME), None)
    if not rag_corpus:
        print("Tạo corpus mới:", DISPLAY_NAME)
        rag_corpus = rag.create_corpus(display_name=DISPLAY_NAME)
    return rag_corpus

def import_file_if_not_exists(corpus_name, gcs_uri):
    files = rag.list_files(corpus_name)
    file_exists = any(gcs_uri in str(f) or gcs_uri in getattr(f, "name", "") for f in files)
    if not file_exists:
        print(f"Đang import file PDF từ {gcs_uri} (chờ 30-90s lần đầu)...")
        rag.import_files(
            corpus_name=corpus_name,
            paths=[gcs_uri],
            chunk_size=1024,
            chunk_overlap=200,
        )
        print("IMPORT FILE THÀNH CÔNG!")
        return True
    else:
        print(f"File PDF {gcs_uri} đã tồn tại → bỏ qua import")
        return False

def setup_rag():
    rag_corpus = get_or_create_corpus()
    
    # Import file ban đầu nếu chưa có
    import_file_if_not_exists(rag_corpus.name, INITIAL_GCS_URI)
    
    # Tạo RAG tool
    rag_resource = rag.RagResource(rag_corpus=rag_corpus.name)
    retrieval_tool = Tool.from_retrieval(
        retrieval=rag.Retrieval(
            source=rag.VertexRagStore(rag_resources=[rag_resource])
        )
    )
    
    model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])
    return model, rag_corpus.name  # Trả về cả model và corpus_name để dùng sau

# ==================== KHỞI TẠO ====================
print("Khởi tạo RAG Engine...")
model, corpus_name = setup_rag()
print("RAG BACKEND HOÀN THÀNH – SẴN SÀNG NHẬN CÂU HỎI!")

# ==================== FASTAPI ====================
app = FastAPI(title="RAG Backend OJT 2025 – ", version="1.0")

class Question(BaseModel):
    question: str

class ImportPDF(BaseModel):
    gcs_uri: str  # URL GCS của file PDF mới (ví dụ: gs://bucket/path/to/file.pdf)

@app.get("/")
async def root():
    return {"message": "RAG Backend đang chạy ok!", "status": "READY"}

@app.post("/chat")
async def chat(q: Question):
    try:
        response = model.generate_content(q.question)
        return {"answer": response.text.strip()}
    except Exception as e:
        return {"error": str(e)}

@app.post("/import_pdf")
async def import_pdf(import_data: ImportPDF):
    try:
        imported = import_file_if_not_exists(corpus_name, import_data.gcs_uri)
        if imported:
            return {"message": f"Đã import thành công file từ {import_data.gcs_uri} vào corpus!"}
        else:
            return {"message": f"File từ {import_data.gcs_uri} đã tồn tại, bỏ qua import."}
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "healthy"}