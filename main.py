# main.py
import os
import json
import tempfile
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

# === FIX CREDENTIAL CHO RENDER.COM (QUAN TRỌNG NHẤT) ===
if os.getenv("GCP_SERVICE_ACCOUNT_JSON"):
    # Render inject JSON qua Secret → tạo file tạm để SDK đọc
    credentials_info = json.loads(os.getenv("GCP_SERVICE_ACCOUNT_JSON"))
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(credentials_info, temp_file)
    temp_file.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_file.name
else:
    # Local (máy bạn) vẫn dùng đường dẫn cũ
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"D:\Project\CapStone\OJT_RAG_CSharp\OJT_RAG.Engine\rag-service-account.json"

import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool
from vertexai.preview.generative_models import rag as rag_tool

# ==================== CẤU HÌNH VERTEX AI ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"   # Region bạn đang dùng

vertexai.init(project=PROJECT_ID, location=LOCATION)

# ==================== TẢI CORPUS & IMPORT FILE (1 lần) ====================
display_name = "ProductDocumentation"
GCS_URI = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"

def setup_rag():
    corpora = rag.list_corpora()
    rag_corpus = next((c for c in corpora if c.display_name == display_name), None)

    if not rag_corpus:
        print("Tạo corpus mới...")
        rag_corpus = rag.create_corpus(display_name=display_name)

    # Kiểm tra file đã import chưa
    files = rag.list_files(rag_corpus.name)
    if not any(getattr(f, "gcs_uri", "") == GCS_URI for f in files):
        print("Đang import file từ GCS (chờ 30-90s)...")
        rag.import_files(
            corpus_name=rag_corpus.name,
            paths=[GCS_URI],
            chunk_size=1024,
            chunk_overlap=200,
        )
        print("IMPORT FILE THÀNH CÔNG!")
    else:
        print("File đã tồn tại → bỏ qua import")

    # Tạo RAG tool
    rag_resource = rag_tool.RagResource(rag_corpus=rag_corpus.name)
    retrieval_tool = Tool.from_retrieval(
        retrieval=rag_tool.Retrieval(
            source=rag_tool.VertexRagStore(
                rag_resources=[rag_resource]
            )
        )
    )

    # Model Gemini 2.5 Pro (mạnh nhất hiện tại)
    model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])
    return model

# Khởi động RAG (chỉ chạy 1 lần khi app start)
print("Đang khởi tạo RAG Engine...")
model = setup_rag()
print("RAG CHATBOT SẴN SÀNG!")

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="RAG Chatbot - Vertex AI + Gemini 2.5 Pro",
    description="Backend cho OJT Capstone - Nhất Triệu",
    version="1.0.0"
)

class QuestionRequest(BaseModel):
    question: str

class AnswerResponse(BaseModel):
    answer: str

@app.get("/")
async def root():
    return {"message": "RAG Backend đang chạy ngon lành!", "status": "ready"}

@app.post("/chat", response_model=AnswerResponse)
async def chat(request: QuestionRequest):
    try:
        response = model.generate_content(request.question)
        return AnswerResponse(answer=response.text)
    except Exception as e:
        return AnswerResponse(answer=f"Lỗi: {str(e)}")

# Health check cho Render
@app.get("/health")
async def health():
    return {"status": "healthy"}