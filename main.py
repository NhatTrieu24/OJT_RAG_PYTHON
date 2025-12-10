# main.py – FINAL + HIỂN THỊ TÊN FILE ĐẸP + CORS FIX
import os
import json
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
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
from vertexai.generative_models import GenerativeModel, Tool, Part, Content

PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"
DISPLAY_NAME = "ProductDocumentation"
INITIAL_GCS = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# ==================== LƯU TÊN FILE (mapping GCS URI → tên thật) ====================
FILE_MAPPING_FILE = "file_mapping.json"

def load_file_mapping() -> Dict[str, str]:
    if os.path.exists(FILE_MAPPING_FILE):
        try:
            with open(FILE_MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_file_mapping(mapping: Dict[str, str]):
    with open(FILE_MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

file_mapping: Dict[str, str] = load_file_mapping()

# ==================== RAG SETUP ====================
corpus = next((c for c in rag.list_corpora() if c.display_name == DISPLAY_NAME), None)
if not corpus:
    print("Tạo corpus mới...")
    corpus = rag.create_corpus(display_name=DISPLAY_NAME)

# Import file đầu tiên + lưu tên thật
files = rag.list_files(corpus.name)
if not any(INITIAL_GCS in str(f) for f in files):
    print("Import file Session 1.pdf...")
    rag.import_files(corpus.name, paths=[INITIAL_GCS])
    # Lưu tên file gốc (tên file trong GCS)
    file_name = INITIAL_GCS.split("/")[-1]
    file_mapping[INITIAL_GCS] = file_name
    save_file_mapping(file_mapping)

# ==================== CORS ====================
app = FastAPI(title="RAG OJT 2025 – HIỂN THỊ TÊN FILE ĐẸP", version="9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== API ====================
class Question(BaseModel):
    question: str

@app.get("/")
async def root():
    return {"message": "RAG Backend OJT – ĐÃ HIỂN THỊ TÊN FILE ĐẸP!", "status": "LIVE"}

@app.post("/chat")
async def chat(q: Question):
    global chat_history
    user_content = Content(role="user", parts=[Part.from_text(q.question)])
    chat_history.append(user_content)
    try:
        response = model.generate_content(chat_history)
        answer = response.text.strip()
        chat_history.append(Content(role="model", parts=[Part.from_text(answer)]))
        save_history(chat_history)
        return {"answer": answer}
    except Exception as e:
        return {"error": str(e)}

@app.get("/history")
async def get_history():
    return {"total": len(chat_history)}

@app.post("/import_pdf")
async def import_pdf(gcs_uri: str = Query(...)):
    try:
        if any(gcs_uri in str(f) for f in rag.list_files(corpus.name)):
            return {"message": "File đã tồn tại"}
        rag.import_files(corpus.name, paths=[gcs_uri])
        # Lưu tên file thật
        file_name = gcs_uri.split("/")[-1]
        file_mapping[gcs_uri] = file_name
        save_file_mapping(file_mapping)
        return {"message": f"Import thành công: {file_name}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/list_files")
async def list_files():
    files = rag.list_files(corpus.name)
    result = []
    for f in files:
        gcs_uri = f.name.split("gs://")[-1] if "gs://" in f.name else f.name
        display_name = file_mapping.get(gcs_uri, gcs_uri.split("/")[-1])
        result.append(display_name)
    return {"files": result}

@app.delete("/delete_file")
async def delete_file(gcs_uri: str = Query(...)):
    try:
        target = next((f for f in rag.list_files(corpus.name) if gcs_uri in f.name), None)
        if target:
            rag.delete_file(name=target.name)
            # Xóa khỏi mapping
            if gcs_uri in file_mapping:
                del file_mapping[gcs_uri]
                save_file_mapping(file_mapping)
            return {"message": f"Đã xóa {gcs_uri}"}
        return {"error": "Không tìm thấy file"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/status")
async def status():
    return {
        "status": "HOÀN HẢO",
        "model": "gemini-2.5-pro",
        "corpus": DISPLAY_NAME,
        "total_files": len(list(rag.list_files(corpus.name))),
        "total_messages": len(chat_history)
    }