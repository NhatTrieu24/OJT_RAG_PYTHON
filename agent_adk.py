import os
import time
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel

# ==================== 1. CẤU HÌNH AUTHENTICATION ====================
key_path = "rag-service-account.json"
if os.path.exists(key_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(key_path)

PROJECT_ID = os.getenv("PROJECT_ID", "reflecting-surf-477600-p4")
LOCATION = os.getenv("LOCATION", "europe-west4")
DB_DSN = os.getenv("DB_DSN")

embedding_model = None
try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
    print("✅ [Agent] Vertex AI & Embedding Model Ready.")
except Exception as e:
    print(f"⚠️ [Agent] Init Error: {e}")

# ==================== 2. HÀM ĐỒNG BỘ VECTOR (UPGRADED) ====================

def sync_missing_embeddings():
    """
    Đồng bộ Vector cho tất cả các bảng nghiệp vụ. 
    Hệ thống sẽ tự động quét các dòng có embedding = NULL để xử lý.
    """
    print("🔄 [System] Bắt đầu quét dữ liệu để đồng bộ Vector...")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # Danh sách các bảng cần Vector hóa dữ liệu
        targets = [
            ("semester", "name", "semester_id"),
            ("major", "major_title", "major_id"),
            ("company", "name", "company_id"),
            ("ojtdocument", "title", "ojtdocument_id"),
            ("job_position", "job_title", "job_position_id"),
            ("job_description", "job_description", "job_description_id"),
            ("finalreport", "student_report_text", "final_report_id"),
            ("companydocument", "title", "company_document_id")
        ]
        
        updated_total = 0
        for table, text_col, id_col in targets:
            existing_cols = get_existing_columns(cur, table)
            
            if text_col in existing_cols and "embedding" in existing_cols:
                cur.execute(f"SELECT {id_col}, {text_col} FROM \"{table}\" WHERE embedding IS NULL")
                rows = cur.fetchall()
                
                if not rows: continue
                
                print(f"   ∟ Bảng [{table}]: Tìm thấy {len(rows)} dòng cần xử lý...")
                for row_id, text in rows:
                    if not text or len(str(text).strip()) < 2: continue
                    
                    vector = get_query_embedding(str(text))
                    if vector:
                        cur.execute(f"UPDATE \"{table}\" SET embedding = %s WHERE {id_col} = %s", (vector, row_id))
                        updated_total += 1
                        
                        # Nghỉ để tránh lỗi Quota 429 của Google Cloud
                        time.sleep(0.5) 
                        
                        if updated_total % 10 == 0:
                            conn.commit()
                            print(f"      - Đã xong {updated_total} dòng...")
            
        conn.commit()
        print(f"✅ [System] Hoàn tất! Tổng cộng cập nhật: {updated_total} Vector.")
            
    except Exception as e:
        print(f"❌ [System] Lỗi đồng bộ: {e}")
    finally:
        if conn: conn.close()

# ==================== 3. HÀM CORE: TẠO VECTOR & SEARCH ====================

def get_query_embedding(text):
    """Chuyển đổi văn bản thành Vector 768 chiều"""
    if not embedding_model or not text: return None
    try:
        # Cắt ngắn text để tránh lỗi Token Limit (Embedding model thường giới hạn ~2048 tokens)
        clean_text = str(text).replace("\n", " ")[:3000]
        embeddings = embedding_model.get_embeddings([clean_text])
        return embeddings[0].values
    except Exception as e:
        print(f"❌ Embedding Error: {e}")
        return None

def get_existing_columns(cur, table_name):
    """Lấy danh sách cột thực tế của bảng để tránh lỗi SQL khi cấu hình thay đổi"""
    try:
        cur.execute(f"SELECT * FROM \"{table_name}\" LIMIT 0")
        return [desc[0] for desc in cur.description]
    except:
        return []

def search_vectors(question, target_table="auto", limit=5):
    """
    Tìm kiếm ngữ nghĩa (Semantic Search) sử dụng Cosine Similarity.
    """
    print(f"🔍 [Search] Phân tích câu hỏi: '{question}'...")
    query_vector = get_query_embedding(question)
    if not query_vector: return "Không thể tạo vector tìm kiếm."

    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # 1. Phân loại ý định thông minh
        tables_to_search = []
        q_lower = question.lower()
        
        mapping = {
            "ojtdocument": ["ojt", "quy định", "hướng dẫn", "quy trình", "biểu mẫu", "hợp đồng", "tài liệu"],
            "job_position": ["việc làm", "tuyển dụng", "job", "lương", "salary", "vị trí", "thực tập", "dev", "engineer"],
            "company": ["công ty", "địa chỉ", "website", "liên hệ", "mã số thuế", "tax"],
            "semester": ["kỳ học", "semester", "spring", "summer", "fall", "thời gian", "bắt đầu"],
            "major": ["ngành", "chuyên ngành", "major", "khối ngành"]
        }

        if target_table in mapping.keys():
            tables_to_search = [target_table]
        else:
            for table, keywords in mapping.items():
                if any(k in q_lower for k in keywords):
                    tables_to_search.append(table)
        
        if not tables_to_search:
            tables_to_search = ["ojtdocument", "job_position"]

        final_results = []
        
        # 2. Truy vấn dữ liệu
        for table in tables_to_search:
            cols = get_existing_columns(cur, table)
            if "embedding" not in cols: continue

            # Định nghĩa các cột quan trọng muốn lấy dữ liệu trả về cho AI
            display_map = {
                "ojtdocument": ["title", "file_url"],
                "job_position": ["job_title", "salary_range", "location", "requirements"],
                "company": ["name", "address", "website"],
                "semester": ["name", "start_date", "end_date"],
                "major": ["major_title", "major_code"]
            }
            
            selected_cols = [c for c in display_map.get(table, cols) if c in cols]
            if not selected_cols: selected_cols = cols[:3]
            
            cols_sql = ", ".join([f"\"{c}\"" for c in selected_cols])
            
            # Sử dụng toán tử <=> (Cosine Distance) của pgvector
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
                if score and score > 0.38: # Ngưỡng chính xác
                    info = " | ".join([f"{selected_cols[i]}: {row[i]}" for i in range(len(selected_cols)) if row[i]])
                    final_results.append(f"[{table.upper()}] {info} (Khớp: {score:.2f})")

        return "\n".join(final_results) if final_results else "KHÔNG TÌM THẤY DỮ LIỆU PHÙ HỢP."

    except Exception as e:
        return f"Lỗi DB: {str(e)}"
    finally:
        if conn: conn.close()

# ==================== 4. LOGIC ĐIỀU PHỐI (ORCHESTRATION) ====================

def run_agent(question: str, file_content: str = None):
    """
    Luồng xử lý RAG: Search DB -> Tạo Context -> AI trả lời
    """
    from rag_core import start_chat_session, get_chat_response
    
    # Tìm kiếm context từ Database
    db_context = search_vectors(question)
    
    # Xây dựng Prompt "Siêu ngữ cảnh"
    prompt = f"""
    Dưới đây là DỮ LIỆU THỰC TẾ từ hệ thống:
    {db_context}
    ---
    Dữ liệu từ file người dùng cung cấp: {file_content if file_content else "N/A"}
    ---
    CÂU HỎI: {question}
    ---
    YÊU CẦU: Dựa vào DỮ LIỆU THỰC TẾ ở trên để trả lời. Nếu không thấy thông tin trong dữ liệu, hãy nói "Tôi không tìm thấy thông tin này trong hệ thống".
    """
    
    chat_session = start_chat_session()
    return get_chat_response(chat_session, prompt), "Mode: RAG Vector Search"

def run_cv_review(cv_text: str, user_message: str):
    """Xử lý Review CV dựa trên các Job thực tế đang có"""
    from rag_core import start_chat_session
    
    matched_jobs = search_vectors(cv_text, target_table="job_position", limit=3)
    
    prompt = f"""
    Bạn là HR chuyên nghiệp. Hãy phân tích CV này: {cv_text[:3000]}
    Dựa trên các vị trí thực tế sau: {matched_jobs}
    Hãy tư vấn cho ứng viên theo yêu cầu: {user_message}
    """
    
    chat_session = start_chat_session()
    response = chat_session.send_message(prompt)
    return response.text, "Mode: CV Reviewer"
