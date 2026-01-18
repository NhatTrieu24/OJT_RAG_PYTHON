import os
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel

# ==================== CẤU HÌNH & INIT ====================
key_path = "rag-service-account.json"
if os.path.exists(key_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(key_path)

PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"
DB_DSN = "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway"

embedding_model = None
try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
    print("✅ [Agent] Vertex AI Ready.")
except Exception as e:
    print(f"⚠️ [Agent] Init Error: {e}")

# ==================== CORE FUNCTIONS ====================

def get_query_embedding(text):
    if not embedding_model: return None
    try:
        return embedding_model.get_embeddings([text[:2000]])[0].values
    except: return None

def search_vectors(question, target_table="auto", limit=5):
    """
    Tìm kiếm thông minh: Tự động chọn bảng và xử lý lỗi NULL an toàn
    """
    print(f"🔍 [Search] Đang tìm: '{question}'...")
    query_vector = get_query_embedding(question)
    if not query_vector: return "Lỗi hệ thống: Không tạo được vector."

    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # 1. LOGIC CHỌN BẢNG THÔNG MINH
        tables_to_search = []
        q_lower = question.lower()
        
        # Tự động phát hiện ý định
        if any(k in q_lower for k in ["ojt", "tài liệu", "quy trình", "hướng dẫn", "giới thiệu", "học kỳ"]):
            tables_to_search.append("ojtdocument")
        
        if any(k in q_lower for k in ["job", "việc", "lương", "tuyển", "vị trí", "dev", "java", "net", "thực tập"]):
            tables_to_search.append("job_position")
            
        # Mặc định tìm cả 2 nếu không rõ
        if not tables_to_search:
            tables_to_search = ["ojtdocument", "job_position"]

        # Nếu AI chỉ định rõ (override)
        if target_table == "ojtdocument": tables_to_search = ["ojtdocument"]
        elif target_table == "job_position": tables_to_search = ["job_position"]

        final_results = []
        
        # 2. CHẠY TÌM KIẾM TRÊN TỪNG BẢNG
        for table in tables_to_search:
            if table == "ojtdocument":
                cols = "title, file_url"
                prefix = "TÀI LIỆU"
            elif table == "job_position":
                cols = "job_title, requirements, location, salary"
                prefix = "CÔNG VIỆC"
            else:
                continue

            # SQL: Thêm điều kiện embedding IS NOT NULL để tránh lỗi
            sql = f"""
                SELECT {cols}, 1 - (embedding <=> %s::vector) as similarity
                FROM "{table}"
                WHERE embedding IS NOT NULL 
                ORDER BY embedding <=> %s::vector
                LIMIT 3;
            """
            cur.execute(sql, (query_vector, query_vector))
            rows = cur.fetchall()
            
            for row in rows:
                # --- SỬA LỖI Ở ĐÂY: Kiểm tra None trước khi dùng ---
                similarity = row[-1]
                
                if similarity is None: 
                    continue # Bỏ qua dòng lỗi

                if similarity > 0.40: # Độ khớp > 40%
                    content = ", ".join([str(item) for item in row[:-1] if item is not None])
                    final_results.append(f"[{prefix}] {content} (Độ khớp: {similarity:.2f})")

        if not final_results:
            return "Không tìm thấy dữ liệu nào phù hợp trong hệ thống."
            
        return "\n".join(final_results)

    except Exception as e:
        print(f"❌ DB Error: {e}")
        return f"Lỗi Database: {e}"
    finally:
        if conn: conn.close()

# ==================== LOGIC CHAT ====================

def run_agent(question: str, file_content: str = None):
    from rag_core import start_chat_session, get_chat_response
    
    prompt = question
    if file_content:
        prompt = f"Thông tin bổ sung:\n{file_content}\n\nCâu hỏi: {question}"

    chat_session = start_chat_session()
    response = get_chat_response(chat_session, prompt)
    return response, "Mode: Vector Search"

def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session
    
    matched_jobs = search_vectors(cv_text, target_table="job_position", limit=3)
    
    prompt = f"""
    Bạn là chuyên gia tuyển dụng. 
    CV Ứng viên: {cv_text[:3000]}
    Job phù hợp: {matched_jobs}
    Câu hỏi: "{user_message}"
    
    Hãy đưa ra lời khuyên và gợi ý job phù hợp.
    """
    
    chat_session = start_chat_session()
    response = chat_session.send_message(prompt)
    return response.text, "Mode: CV Review"