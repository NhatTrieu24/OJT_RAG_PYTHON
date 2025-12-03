# main.py – FINAL FINAL, CHẠY 1000000% (đã test lúc 15:30 ngày 03/12/2025)
import os
import json
from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List, Dict

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
from vertexai.preview.generative_models import (
    GenerativeModel,
    Tool,
    Part,
    Content,
    GENERATION_ROLE_USER,
    GENERATION_ROLE_MODEL
)

PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"
DISPLAY_NAME = "ProductDocumentation"
INITIAL_GCS = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# ==================== LỊCH SỬ CHAT LƯU FILE ====================
HISTORY_FILE = "chat_history.json"

def load_history() -> List[Content]:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [Content(**item) for item in data]
        except:
            return []
    return []

def save_history(history: List[Content]):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump([h.dict() for h in history[-40:]], f, ensure_ascii=False, indent=2)

chat_history: List[Content] = load_history()

# ==================== RAG SETUP ====================
def get_corpus():
    corpora = rag.list_corpora()
    corpus = next((c for c in corpora if c.display_name == DISPLAY_NAME), None)
    if not corpus:
        print(f"Tạo corpus mới: {DISPLAY_NAME}")
        corpus = rag.create_corpus(display_name=DISPLAY_NAME)
    return corpus

corpus = get_corpus()

files = rag.list_files(corpus.name)
if not any(INITIAL_GCS in str(f) for f in files):
    print("Đang import file Session 1.pdf...")
    rag.import_files(corpus.name, paths=[INITIAL_GCS])
    print("Import file đầu tiên thành công!")

rag_resource = rag.RagResource(rag_corpus=corpus.name)
retrieval_tool = Tool.from_retrieval(
    retrieval=rag.Retrieval(source=rag.VertexRagStore(rag_resources=[rag_resource]))
)

model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])

# ==================== FASTAPI ====================
app = FastAPI(title="RAG OJT 2025 – FINAL CLEAN", version="5.0")

class Question(BaseModel):
    question: str

@app.get("/")
async def root():
    return {"message": "RAG Backend OJT – FINAL CLEAN, READY!", "status": "LIVE"}

@app.post("/chat")
async def chat(q: Question):
    global chat_history
    chat_history.append(Content(role=GENERATION_ROLE_USER, parts=[Part.from_text(q.question)]))
    try:
        response = model.generate_content(chat_history)
        answer = response.text.strip()
        chat_history.append(Content(role=GENERATION_ROLE_MODEL, parts=[Part.from_text(answer)]))
        save_history(chat_history)
        return {"answer": answer}
    except Exception as e:
        return {"error": str(e)}

@app.get("/history")
async def get_history():
    return {"history": [h.dict() for h in chat_history[-20:]], "total": len(chat_history)}

@app.post("/import_pdf")
async def import_pdf(gcs_uri: str = Query(...)):
    try:
        if any(gcs_uri in str(f) for f in rag.list_files(corpus.name)):
            return {"message": f"File {gcs_uri} đã tồn tại"}
        rag.import_files(corpus.name, paths=[gcs_uri])
        return {"message": f"Import thành công: {gcs_uri}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/list_files")
async def list_files():
    files = rag.list_files(corpus.name)
    return {"files": [f.name.split("/")[-1] for f in files]}

@app.delete("/delete_file")
async def delete_file(gcs_uri: str = Query(...)):
    try:
        files = rag.list_files(corpus.name)
        target = next((f for f in files if gcs_uri in f.name), None)
        if target:
            rag.delete_file(name=target.name)
            return {"message": f"Đã xóa {gcs_uri}"}
        return {"error": "Không tìm thấy file"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/status")
async def status():
    files = rag.list_files(corpus.name)
    return {
        "status": "healthy",
        "model": "gemini-2.5-pro",
        "corpus": DISPLAY_NAME,
        "total_files": len(files),
        "total_messages": len(chat_history)
    }

@app.get("/health")
async def health():
    return {"status": "ok"}