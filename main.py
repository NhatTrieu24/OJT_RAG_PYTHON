# main.py – PHIÊN BẢN CUỐI CÙNG, CHẠY 100% TRÊN RENDER & LOCAL
# ĐÃ FIX: secret file, fallback ngu, lỗi 401, lỗi file not found

import os
from fastapi import FastAPI
from pydantic import BaseModel

# ==================== CREDENTIALS – CHỐNG NGU 100% CHO RENDER ====================
secret_path = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"

if os.path.exists(secret_path) and os.path.getsize(secret_path) > 0:
    print("Render detect secret file → dùng secret từ Render")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = secret_path
else:
    # Local fallback (chỉ dùng khi chạy trên máy bạn)
    local_path = os.path.expanduser("~/rag-service-account.json")  # hoặc đổi thành đường dẫn của bạn
    if os.path.exists(local_path):
        print("Chạy local → dùng file key ở home folder")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = local_path
    else:
        raise FileNotFoundError(
            "\nKHÔNG TÌM THẤY SERVICE ACCOUNT KEY!\n"
            "• Trên Render: phải thêm Secret File tên GCP_SERVICE_ACCOUNT_JSON\n"
            "• Trên local: đặt file JSON vào ~/rag-service-account.json hoặc sửa đường dẫn trong code\n"
        )

# ==================== IMPORT VERTEX AI RAG ====================
import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool

# ==================== CẤU HÌNH ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"        # europe-west4 đã GA, không cần whitelist
DISPLAY_NAME = "ProductDocumentation"
GCS_URI = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# ==================== RAG SETUP ====================
def get_or_create_corpus():
    print("Đang kiểm tra/khởi tạo RAG Corpus...")
    corpora = rag.list_corpora()
    rag_corpus = next((c for c in corpora if c.display_name == DISPLAY_NAME), None)
    if not rag_corpus:
        print(f"Không tìm thấy corpus '{DISPLAY_NAME}' → tạo mới")
        rag_corpus = rag.create_corpus(display_name=DISPLAY_NAME)
    else:
        print(f"Đã tìm thấy corpus: {DISPLAY_NAME}")
    return rag_corpus

def import_initial_file_if_needed(corpus_name: str):
    files = rag.list_files(corpus_name)
    file_exists = any(GCS_URI in str(f) for f in files)
    if not file_exists:
        print(f"Đang import file PDF ban đầu từ GCS (chờ 30-90s lần đầu)...")
        rag.import_files(
            corpus_name=corpus_name,
            paths=[GCS_URI],
            chunk_size=1024,
            chunk_overlap=200,
        )
        print("IMPORT FILE BAN ĐẦU THÀNH CÔNG!")
    else:
        print("File PDF ban đầu đã tồn tại → bỏ qua")

def setup_rag():
    corpus = get_or_create_corpus()
    import_initial_file_if_needed(corpus.name)

    # Tạo retrieval tool
    rag_resource = rag.RagResource(rag_corpus=corpus.name)
    retrieval_tool = Tool.from_retrieval(
        retrieval=rag.Retrieval(
            source=rag.VertexRagStore(rag_resources=[rag_resource])
        )
    )

    # Dùng gemini-1.5-pro (ổn định hơn gemini-2.5-pro ở một số region)
    model = GenerativeModel("gemini-1.5-pro", tools=[retrieval_tool])
    return model

# ==================== KHỞI TẠO RAG ====================
print("Khởi tạo RAG Engine với Gemini 1.5 Pro...")
model = setup_rag()
print("RAG BACKEND HOÀN THÀNH 100% – SẴN SÀNG NHẬN CÂU HỎI!")

# ==================== FASTAPI APP ====================
app = FastAPI(title="RAG Backend OJT 2025 – Nhất Triệu", version="2.0")

class Question(BaseModel):
    question: str

class ImportPDF(BaseModel):
    gcs_uri: str

@app.get("/")
async def root():
    return {"message": "RAG Backend đang chạy cực ngon!", "status": "READY"}

@app.post("/chat")
async def chat(q: Question):
    try:
        response = model.generate_content(q.question)
        return {"answer": response.text.strip()}
    except Exception as e:
        return {"error": str(e)}

@app.post("/import_pdf")
async def import_pdf(data: ImportPDF):
    try:
        # Lấy corpus hiện tại
        corpora = rag.list_corpora()
        corpus = next((c for c in corpora if c.display_name == DISPLAY_NAME), None)
        if not corpus:
            return {"error": "Không tìm thấy corpus!"}
        
        imported = any(data.gcs_uri in str(f) for f in rag.list_files(corpus.name))
        if imported:
            return {"message": f"File {data.gcs_uri} đã tồn tại → bỏ qua"}
        
        print(f"Đang import file mới: {data.gcs_uri}")
        rag.import_files(corpus_name=corpus.name, paths=[data.gcs_uri])
        return {"message": f"Đã import thành công {data.gcs_uri}!"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "healthy", "rag": "ready"}