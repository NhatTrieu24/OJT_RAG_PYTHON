import os
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"D:\Project\CapStone\OJT_RAG_CSharp\OJT_RAG.Engine\rag-service-account.json"

import vertexai
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel, Tool

PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# Corpus (đã có)
display_name = "ProductDocumentation"
corpora = rag.list_corpora()
rag_corpus = next((c for c in corpora if c.display_name == display_name), None)
print(f"Đang dùng corpus: {rag_corpus.name}")

# Import file (đã thành công rồi)
GCS_URI = "gs://cloud-ai-platform-2b8ffe9f-38d5-43c4-b812-fc8cebcc659f/Session 1.pdf"
files = rag.list_files(rag_corpus.name)
if any(getattr(f, "gcs_uri", "") == GCS_URI for f in files):
    print("File đã được import rồi")
else:
    print("Đang import...")
    rag.import_files(corpus_name=rag_corpus.name, paths=[GCS_URI])
    print("IMPORT FILE THÀNH CÔNG!!!")

# TOOL
rag_tool = Tool.from_retrieval(
    retrieval=rag.Retrieval(
        source=rag.VertexRagStore(
            rag_resources=[rag.RagResource(rag_corpus=rag_corpus.name)]
        )
    )
)

# DÙNG DÒNG NÀY LÀ CHẠY NGON 100%
model = GenerativeModel("gemini-2.5-pro", tools=[rag_tool])
# hoặc thử: "gemini-1.5-pro-002" hoặc "gemini-1.5-pro"

# CHAT
print("\n" + "="*80)
print("RAG CHATBOT HOÀN CHỈNH – BẠN ĐÃ LÀM ĐƯỢC RỒI!!!")
print("="*80 + "\n")

while True:
    q = input("Câu hỏi: ").strip()
    if q.lower() in ["exit", "quit", "thoát"]:
        print("Tạm biệt! Chúc mừng bạn hoàn thành OJT RAG xịn nhất lớp!")
        break
    try:
        resp = model.generate_content(q)
        print(f"\nTrả lời:\n{resp.text}\n")
        print("-" * 80)
    except Exception as e:
        print(f"Lỗi: {e}")