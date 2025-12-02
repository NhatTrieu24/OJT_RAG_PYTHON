# main.py – PHIÊN BẢN HOÀN CHỈNH CUỐI CÙNG – CHẠY NGON CẢ LOCAL + RENDER
import os
import json
import tempfile
from fastapi import FastAPI
from pydantic import BaseModel

# === FIX CREDENTIAL CHO RENDER.COM (BẮT BUỘC) ===
if os.getenv("GCP_SERVICE_ACCOUNT_JSON"):
    credentials_info = json.loads(os.getenv("GCP_SERVICE_ACCOUNT_JSON"))
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(credentials_info, temp_file)
    temp_file.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_file.name
else:
    # Local (máy bạn)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"D:\Project\CapStone\OJT_RAG_CSharp\OJT_RAG.Engine\rag-service-account.json"

# === IMPORT ĐÚNG CÁCH MỚI NHẤT 2025 ===
import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool

# === CẤU HÌNH ===
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"

vertexai.init(project=PROJECT_ID, location=LOCATION)

display_name = "ProductDocumentation"
GCS_URI = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"

def setup_rag():
    print("Đang kiểm tra corpus...")
    corpora = rag.list_corpora()
    rag_corpus = next((c for c in corpora if c.display_name == display_name), None)

    if not rag_corpus:
        print("Tạo corpus mới...")
        rag_corpus = rag.create_corpus(display_name=display_name)

    # Import file nếu chưa có
    files = rag.list_files(rag_corpus.name)
    if not any(getattr(f, "gcs_uri", "") == GCS_URI for f in files):
        print("Đang import file PDF từ GCS (chờ 30-90s)...")
        rag.import_files(
            corpus_name=rag_corpus.name,
            paths=[GCS_URI],
            chunk_size=1024,
            chunk_overlap=200,
        )
        print("IMPORT FILE THÀNH CÔNG!")

    # TẠO RAG TOOL ĐÚNG CÁCH MỚI NHẤT (KHÔNG DÙNG generative_models.rag)
    retrieval_tool = Tool.from_retrieval(
        retrieval=rag.Retrieval(
            source=rag.VertexRagStore(
                rag_resources=[
                    rag.RagResource(rag_corpus=rag_corpus.name)
                ]
            )
        )
    )

    model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])
    return model

print("Khởi tạo RAG Engine...")
model = setup_rag()
print("RAG BACKEND SẴN SÀNG 100% – CHỜ YÊU CẦU TỪ FRONTEND!")

# === FASTAPI APP ===
app = FastAPI(
    title="RAG Backend Capstone 2025",
    description="Backend RAG dùng Vertex AI + Gemini 2.5 Pro",
    version="1.0"
)

class Query(BaseModel):
    question: str

@app.get("/")
async def root():
    return {"message": "RAG Backend đang chạy cực mượt!", "model": "gemini-2.5-pro"}

@app.post("/chat")
async def chat(query: Query):
    try:
        response = model.generate_content(query.question)
        return {"answer": response.text}
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "healthy"}