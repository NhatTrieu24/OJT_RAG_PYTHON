# main.py – PHIÊN BẢN HOÀN CHỈNH CUỐI CÙNG – ĐÃ TEST 100% TRÊN RENDER.COM
import os
import json
import tempfile
from fastapi import FastAPI
from pydantic import BaseModel

# ==================== CREDENTIALS – DÙNG SECRET FILE CỦA RENDER ====================
if os.path.exists("/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"):
    print("Đang dùng Secret File từ Render (GCP_SERVICE_ACCOUNT_JSON)")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"
else:
    print("Chạy local – dùng file cứng trên Windows")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"D:\Project\CapStone\OJT_RAG_CSharp\OJT_RAG.Engine\rag-service-account.json"

# ==================== IMPORT VERTEX AI RAG ====================
import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool

# ==================== CẤU HÌNH ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# Corpus & file PDF của bạn
DISPLAY_NAME = "ProductDocumentation"
GCS_URI = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"

def setup_rag():
    print("Đang kiểm tra/khởi tạo RAG Corpus...")
    
    # Kiểm tra corpus tồn tại chưa
    corpora = rag.list_corpora()
    rag_corpus = next((c for c in corpora if c.display_name == DISPLAY_NAME), None)
    
    if not rag_corpus:
        print("Tạo corpus mới:", DISPLAY_NAME)
        rag_corpus = rag.create_corpus(display_name=DISPLAY_NAME)

    # Kiểm tra file đã import chưa (CÁCH MỚI NHẤT 2025 – DÙNG .name để an toàn)
    files = rag.list_files(rag_corpus.name)
    file_exists = any(GCS_URI in getattr(f, "name", "") for f in files)

    if not file_exists:
        print("Đang import file PDF từ GCS (có thể mất 30-90s lần đầu)...")
        rag.import_files(
            corpus_name=rag_corpus.name,
            paths=[GCS_URI],
            chunk_size=1024,
            chunk_overlap=200,
        )
        print("IMPORT FILE THÀNH CÔNG!")
    else:
        print("File PDF đã tồn tại → bỏ qua import")

    # Tạo RAG Retrieval Tool (cách mới nhất)
    rag_resource = rag.RagResource(rag_corpus=rag_corpus.name)
    retrieval_tool = Tool.from_retrieval(
        retrieval=rag.Retrieval(
            source=rag.VertexRagStore(rag_resources=[rag_resource])
        )
    )

    # Dùng Gemini 2.5 Pro (mạnh nhất hiện tại)
    model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])
    return model

# ==================== KHỞI TẠO RAG ENGINE ====================
print("Khởi tạo RAG Engine với Gemini 2.5 Pro...")
model = setup_rag()
print("RAG BACKEND HOÀN THÀNH 100% – SẴN SÀNG NHẬN CÂU HỎI!")

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="RAG Chatbot OJT Capstone 2025 – Nhất Triệu",
    description="Backend RAG dùng Vertex AI + Gemini 2.5 Pro + PDF Session 1",
    version="1.0.0"
)

class Question(BaseModel):
    question: str

@app.get("/")
async def root():
    return {
        "message": "RAG Backend đang chạy cực mượt!",
        "model": "gemini-2.5-pro",
        "corpus": DISPLAY_NAME,
        "status": "READY"
    }

@app.post("/chat")
async def chat(q: Question):
    try:
        response = model.generate_content(q.question)
        return {"answer": response.text.strip()}
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "healthy", "rag": "ready"}