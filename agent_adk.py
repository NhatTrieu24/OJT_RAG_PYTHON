import os
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel

# ==================== CẤU HÌNH & INIT ====================
key_path = "rag-service-account.json"
if os.path.exists(key_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(key_path)

PROJECT_ID = os.getenv("PROJECT_ID", "reflecting-surf-477600-p4")
LOCATION = os.getenv("LOCATION", "europe-west4")
DB_DSN = os.getenv("DB_DSN", "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway")

embedding_model = None
try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
    print("✅ [Agent] Vertex AI Ready.")
except Exception as e:
    print(f"⚠️ [Agent] Init Error: {e}")

 #==================== SYNC MISSING EMBEDDINGS ====================
import time # Thêm import này ở đầu file

def sync_missing_embeddings():
    """Đồng bộ Vector có cơ chế nghỉ để tránh lỗi Quota 429"""
    print("🔄 [System] Đang kiểm tra dữ liệu mới để đồng bộ Vector...")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
       # Thêm vào targets trong agent_adk.py
        targets = [
    ("semester", "name", "semester_id"),
    ("major", "major_title", "major_id"),
    ("company", "name", "company_id"),
    ("ojtdocument", "title", "ojtdocument_id"),
    ("job_position", "job_title", "job_position_id"),
    ("job_description", "job_description", "job_description_id"),
    ("finalreport", "student_report_text", "finalreport_id"),
    ("companydocument", "title", "companydocument_id")
                ]
        
        updated_count = 0
        for table, text_col, id_col in targets:
            existing_cols = get_existing_columns(cur, table)
            
            if text_col in existing_cols and "embedding" in existing_cols:
                cur.execute(f"SELECT {id_col}, {text_col} FROM \"{table}\" WHERE embedding IS NULL")
                rows = cur.fetchall()
                
                for row_id, text in rows:
                    if not text: continue
                    
                    vector = get_query_embedding(text)
                    if vector:
                        cur.execute(f"UPDATE \"{table}\" SET embedding = %s WHERE {id_col} = %s", (vector, row_id))
                        updated_count += 1
                        
                        # NGHỈ 1 GIÂY giữa các request để không bị Google chặn
                        time.sleep(1) 
                        
                        # Commit mỗi 5 dòng để đảm bảo dữ liệu được lưu dần
                        if updated_count % 5 == 0:
                            conn.commit()
                            print(f"   ∟ Đã xử lý {updated_count} dòng...")
            
        conn.commit()
        print(f"✅ [System] Hoàn tất! Đã cập nhật thêm {updated_count} vector.")
            
    except Exception as e:
        print(f"❌ [System] Lỗi đồng bộ: {e}")
    finally:
        if conn: conn.close()
# ==================== CORE FUNCTIONS ====================

def get_query_embedding(text):
    if not embedding_model or not text: return None
    try:
        # Cắt ngắn text để tránh quá giới hạn token của model
        return embedding_model.get_embeddings([text[:2000]])[0].values
    except Exception as e:
        print(f"❌ Embedding Error: {e}")
        return None

def get_existing_columns(cur, table_name):
    """Kiểm tra các cột thực tế để tránh lỗi UndefinedColumn"""
    try:
        # Sử dụng ngoặc kép để xử lý bảng có tên đặc biệt như "User"
        cur.execute(f"SELECT * FROM \"{table_name}\" LIMIT 0")
        return [desc[0] for desc in cur.description]
    except:
        return []

def search_vectors(question, target_table="auto", limit=5):
    """
    Tìm kiếm thông minh trên nhiều bảng sử dụng PGVector.
    """
    print(f"🔍 [Search] Đang tìm: '{question}'...")
    query_vector = get_query_embedding(question)
    if not query_vector: 
        return "Hệ thống đang gặp sự cố khi tạo vector tìm kiếm."

    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # 1. PHÂN LOẠI Ý ĐỊNH ĐỂ CHỌN BẢNG
        tables_to_search = []
        q_lower = question.lower()
        
        # Mapping từ khóa -> Bảng
        mapping = {
            "ojtdocument": ["ojt", "tài liệu", "quy định", "hướng dẫn", "quy trình", "biểu mẫu", "hợp đồng"],
            "job_position": ["job", "việc làm", "tuyển dụng", "vị trí", "thực tập", "lương", "salary", "dev", "engineer"],
            "company": ["công ty", "địa chỉ", "website", "liên hệ", "email", "tax", "mã số thuế"],
            "semester": ["kỳ học", "học kỳ", "semester", "spring", "summer", "fall", "bắt đầu", "kết thúc"],
            "major": ["ngành", "chuyên ngành", "major", "học về gì"]
        }

        for table, keywords in mapping.items():
            if any(k in q_lower for k in keywords):
                tables_to_search.append(table)
        
        # Nếu không bắt được từ khóa hoặc AI yêu cầu tìm bảng cụ thể
        if target_table in mapping.keys():
            tables_to_search = [target_table]
        elif not tables_to_search:
            tables_to_search = ["ojtdocument", "job_position", "company"]

        final_results = []
        
        # 2. TRUY VẤN VECTOR TRÊN CÁC BẢNG ĐÃ CHỌN
        for table in tables_to_search:
            existing_cols = get_existing_columns(cur, table)
            if not existing_cols or "embedding" not in existing_cols:
                continue

            # Ưu tiên các cột chứa thông tin quan trọng để trả về cho AI
            priority_cols = [
                "title", "name", "job_title", "fullname", "major_title",
                "requirements", "address", "website", "salary_range", "start_date"
            ]
            valid_cols = [c for c in priority_cols if c in existing_cols]
            if not valid_cols:
                valid_cols = [c for c in existing_cols if c != 'embedding'][:3]

            cols_sql = ", ".join([f"\"{c}\"" for c in valid_cols])
            
            # Câu lệnh SQL Vector Search (Cosine distance)
            sql = f"""
                SELECT {cols_sql}, 1 - (embedding <=> %s::vector) as similarity
                FROM "{table}"
                WHERE embedding IS NOT NULL 
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
            """
            cur.execute(sql, (query_vector, query_vector, limit))
            rows = cur.fetchall()
            
            for row in rows:
                score = row[-1]
                # Ngưỡng similarity 0.35 là mức trung bình an toàn cho Tiếng Việt
                if score and score > 0.35:
                    info = " | ".join([f"{valid_cols[i]}: {row[i]}" for i in range(len(valid_cols)) if row[i]])
                    final_results.append(f"[{table.upper()}] {info} (Score: {score:.2f})")

        if not final_results:
            return "HỆ THỐNG: Không tìm thấy dữ liệu liên quan trong kho lưu trữ."
            
        return "\n".join(final_results)

    except Exception as e:
        print(f"❌ DB Error: {e}")
        return f"Lỗi truy vấn cơ sở dữ liệu: {str(e)}"
    finally:
        if conn: conn.close()

# ==================== LOGIC CHAT & REVIEW ====================

def run_agent(question: str, file_content: str = None):
    from rag_core import start_chat_session, get_chat_response
    
    # Lấy dữ liệu thực tế từ DB qua Vector Search
    db_context = search_vectors(question)
    
    # Kết hợp context từ file (nếu có) và dữ liệu từ DB
    full_prompt = f"DỮ LIỆU TỪ DATABASE:\n{db_context}\n\n"
    if file_content:
        full_prompt += f"DỮ LIỆU TỪ FILE UPLOAD:\n{file_content}\n\n"
    full_prompt += f"CÂU HỎI NGƯỜI DÙNG: {question}"

    chat_session = start_chat_session()
    response = get_chat_response(chat_session, full_prompt)
    return response, "Mode: Vector Search"

def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session
    
    # Tìm job phù hợp với CV trong DB
    matched_jobs = search_vectors(cv_text, target_table="job_position", limit=3)
    
    prompt = f"""
    Bạn là một chuyên gia HR. Hãy thực hiện 2 nhiệm vụ:
    1. Nhận xét CV: {cv_text[:3000]}
    2. Dựa vào danh sách Job sau: {matched_jobs}, hãy tư vấn vị trí phù hợp nhất.
    3. Trả lời yêu cầu riêng của ứng viên: {user_message}
    """
    
    chat_session = start_chat_session()
    response = chat_session.send_message(prompt)
    return response.text, "Mode: CV Review"