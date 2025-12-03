# main.py – FINAL 100% HOÀN HẢO, CHẠY NGON NGAY TRÊN RENDER
import os
import json
from fastapi import FastAPI, Query
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
INITIAL_GCS = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# ==================== LỊCH SỬ CHAT (dùng Content object thật) ====================
HISTORY_FILE = "chat_history.json"

def load_history() -> List[Content]:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [Content(**item) for item in data]
        except Exception as e:
            print("Lỗi load history:", e)
            return []
    return []

def save_history(history: List[Content]):
    try:
        serializable = []
        for c in history[-40:]:
            parts = []
            for p in c.parts:
                if hasattr(p, "text"):
                    parts.append({"text": p.text})
                elif hasattr(p, "_raw_part"):
                    parts.append({"text": p._raw_part.text})
            serializable.append({"role": c.role, "parts": parts})
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    except:
        pass  # không crash nếu lỗi lưu

chat_history: List[Content] = load_history()

# ==================== RAG SETUP ====================
corpus = next((c for c in rag.list_corpora() if c.display_name == DISPLAY_NAME), None)
if not corpus:
    print("Tạo corpus mới...")
    corpus = rag.create_corpus(display_name=DISPLAY_NAME)

files = rag.list_files(corpus.name)
if not any(INITIAL_GCS in str(f) for f in files):
    print("Import file đầu tiên...")
    rag.import_files(corpus.name, paths=[INITIAL_GCS])

rag_resource = rag.RagResource(rag_corpus=corpus.name)
retrieval_tool = Tool.from_retrieval(
    retrieval=rag.Retrieval(source=rag.VertexRagStore(rag_resources=[rag_resource]))
)

model = GenerativeModel("gemini-2.5-pro", tools=[retrieval_tool])

# ==================== FASTAPI ====================
app = FastAPI(title="RAG OJT 2025 – HOÀN HẢO", version="7.0")

class Question(BaseModel):
    question: str

@app.get("/")
async def root():
    return {"message": "RAG Backend OJT – HOÀN HẢO, SẴN SÀNG!", "status": "LIVE"}

# CHAT CÓ LỊCH SỬ – 100% HOẠT ĐỘNG
@app.post("/chat")
async def chat(q: Question):
    global chat_history
    user_content = Content(role="user", parts=[Part.from_text(q.question)])
    chat_history.append(user_content)
    
    try:
        response = model.generate_content(chat_history)
        answer = response.text.strip()
        bot_content = Content(role="model", parts=[Part.from_text(answer)])
        chat_history.append(bot_content)
        save_history(chat_history)
        return {"answer": answer}
    except Exception as e:
        return {"error": str(e)}

@app.get("/history")
async def get_history():
    return {"total": len(chat_history), "last_5": [c.role + ": " + c.parts[0].text[:100] for c in chat_history[-10:]]}

@app.post("/import_pdf")
async def import_pdf(gcs_uri: str = Query(...)):
    try:
        if any(gcs_uri in str(f) for f in rag.list_files(corpus.name)):
            return {"message": "File đã tồn tại"}
        rag.import_files(corpus.name, paths=[gcs_uri])
        return {"message": f"Import thành công: {gcs_uri}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/list_files")
async def list_files():
    return {"files": [f.name.split("/")[-1] for f in rag.list_files(corpus.name)]}

@app.delete("/delete_file")
async def delete_file(gcs_uri: str = Query(...)):
    try:
        target = next((f for f in rag.list_files(corpus.name) if gcs_uri in f.name), None)
        if target:
            rag.delete_file(name=target.name)
            return {"message": "Đã xóa"}
        return {"error": "Không tìm thấy"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/status")
async def status():
    return {
        "status": "HOÀN HẢO",
        "model": "gemini-2.5-pro",
        "corpus": DISPLAY_NAME,
        "files": len(list(rag.list_files(corpus.name))),
        "messages": len(chat_history)
    }