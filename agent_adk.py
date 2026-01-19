import os
import time
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel
from tenacity import retry, stop_after_attempt, wait_exponential

# ==================== 1. CẤU HÌNH ====================
key_path = "rag-service-account.json"
if os.path.exists(key_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(key_path)

PROJECT_ID = os.getenv("PROJECT_ID", "reflecting-surf-477600-p4")
LOCATION = os.getenv("LOCATION", "us-west1")
DB_DSN = os.getenv("DB_DSN","postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway")

embedding_model = None
try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
    print("✅ [Agent] Vertex AI & Embedding Model Ready.")
except Exception as e:
    print(f"⚠️ [Agent] Init Error: {e}")

# ==================== 2. EMBEDDING (BATCH & RETRY) ====================

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60))
def get_embeddings_batch(texts):
    if not embedding_model or not texts: return []
    # Làm sạch văn bản để tránh lỗi định dạng
    clean_texts = [str(t).replace("\n", " ").strip()[:3000] for t in texts if t]
    if not clean_texts: return []
    try:
        embeddings = embedding_model.get_embeddings(clean_texts)
        return [e.values for e in embeddings]
    except Exception as e:
        print(f"⚠️ API Warning: {e}. Đang thử lại...")
        raise e

def get_query_embedding(text):
    res = get_embeddings_batch([text])
    return res[0] if res else None

# ==================== 3. ĐỒNG BỘ VECTOR TOÀN DIỆN ====================

def get_existing_columns(cur, table_name):
    try:
        cur.execute(f'SELECT * FROM "{table_name}" LIMIT 0')
        return [desc[0] for desc in cur.description]
    except: return []

def sync_missing_embeddings():
    print("🔄 [System] Đang đồng bộ Vector cho toàn bộ Schema...")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        targets = [
            ("semester", "name", "semester_id"),
            ("major", "major_title", "major_id"),
            ("company", "name", "company_id"),
            ("ojtdocument", "title", "ojtdocument_id"),
            ("companydocument", "title", "companydocument_id"),
            ("job_position", "job_title", "job_position_id"),
            ("job_description", "job_description", "job_description_id"),
            ("job_title_overview", "job_title", "job_title_id"),
            ("finalreport", "student_report_text", "finalreport_id"),
            ("message", "content", "message_id"),
            ("User", "fullname", "user_id")
        ]
        
        total = 0
        for table, text_col, id_col in targets:
            cols = get_existing_columns(cur, table)
            if text_col not in cols or "embedding" not in cols: continue

            cur.execute(f'SELECT "{id_col}", "{text_col}" FROM "{table}" WHERE embedding IS NULL AND "{text_col}" IS NOT NULL')
            rows = cur.fetchall()
            if not rows: continue
            
            print(f"   ∟ Bảng [{table}]: Xử lý {len(rows)} dòng.")
            batch_size = 50
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i+batch_size]
                v = get_embeddings_batch([r[1] for r in batch])
                for j, vec in enumerate(v):
                    cur.execute(f'UPDATE "{table}" SET embedding = %s WHERE "{id_col}" = %s', (vec, batch[j][0]))
                conn.commit()
                total += len(batch)
                time.sleep(12) # Lách Quota 429
        print(f"🎉 [System] Hoàn tất đồng bộ {total} dòng.")
    except Exception as e: print(f"❌ Lỗi đồng bộ: {e}")
    finally:
        if conn: conn.close()

# ==================== 4. SEARCH & RAG LOGIC (FIXED) ====================

def search_vectors(question, target_table="auto", limit=5):
    query_vector = get_query_embedding(question)
    if not query_vector: return ""

    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # 1. Mở rộng bộ lọc Mapping để bao quát các bảng quan trọng
        mapping = {
            "semester": ["kỳ", "học kỳ", "semester", "spring", "summer", "fall", "2025", "2024", "bắt đầu", "kết thúc"],
            "company": ["công ty", "địa chỉ", "website", "fpt", "momo", "viettel", "liên hệ"],
            "ojtdocument": ["ojt", "quy định", "hướng dẫn", "tài liệu", "quy trình", "biểu mẫu"],
            "job_position": ["việc làm", "tuyển dụng", "job", "lương", "salary", "thực tập", "vị trí"]
        }

        tables_to_search = []
        q_lower = question.lower()
        for tbl, keywords in mapping.items():
            if any(k in q_lower for k in keywords):
                tables_to_search.append(tbl)
        
        # FIX: Nếu hỏi về thời gian/kỳ học nhưng Mapping chưa bắt được bảng semester
        if any(k in q_lower for k in ["khi nào", "thời gian", "kỳ học", "bắt đầu"]):
            if "semester" not in tables_to_search:
                tables_to_search.append("semester")

        # 2. Nếu vẫn không thấy bảng nào, buộc phải tìm ở 3 bảng cốt lõi
        if not tables_to_search:
            tables_to_search = ["ojtdocument", "job_position", "semester", "company"]

        final_results = []
        # Loại bỏ bảng trùng lặp
        tables_to_search = list(set(tables_to_search))

        for table in tables_to_search:
            cols = get_existing_columns(cur, table)
            if "embedding" not in cols: continue

            # Schema hiển thị chuẩn cho từng bảng
            d_map = {
                "semester": ["name", "start_date", "end_date", "is_active"],
                "company": ["name", "address", "website"],
                "ojtdocument": ["title", "file_url"],
                "job_position": ["job_title", "location", "salary_range"]
            }
            s_cols = [c for c in d_map.get(table, []) if c in cols] or cols[:2]
            cols_sql = ", ".join([f'"{c}"' for c in s_cols])

            # Thực hiện Vector Search (Cosine Similarity)
            sql = f'SELECT {cols_sql}, 1 - (embedding <=> %s::vector) as sim FROM "{table}" WHERE embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT 3'
            cur.execute(sql, (query_vector, query_vector))
            
            for r in cur.fetchall():
                score = r[-1]
                # Nới lỏng ngưỡng cho các từ khóa ngắn hoặc tên riêng (Spring, MoMo, FPT)
                threshold = 0.30 if (len(q_lower) < 15 or any(k in q_lower for k in ["spring", "momo", "fpt"])) else 0.38
                
                if score and score > threshold:
                    # Lọc bỏ nhiễu lỗi kỹ thuật
                    if any(err in str(r[0]).lower() for err in ["lỗi", "error", "undefined"]): continue
                    
                    content = " | ".join([f"{s_cols[j]}: {r[j]}" for j in range(len(s_cols)) if r[j]])
                    final_results.append(f"[{table.upper()}] {content}")

        return "\n".join(final_results)
    except Exception as e:
        print(f"❌ Search Error: {e}")
        return ""
    finally:
        if conn: conn.close()

def run_agent(question: str, file_content: str = None):
    from rag_core import start_chat_session, get_chat_response
    
    # 1. Trích xuất context
    db_context = search_vectors(question)
    
    # 2. Xây dựng Prompt chặt chẽ
    prompt = f"""
    DỮ LIỆU HỆ THỐNG (BẮT BUỘC SỬ DỤNG):
    {db_context if db_context else "Không tìm thấy dữ liệu liên quan trong DB."}
    
    FILE NGƯỜI DÙNG: {file_content if file_content else "N/A"}
    
    CÂU HỎI: {question}
    
    YÊU CẦU:
    - Nếu có dữ liệu trong 'DỮ LIỆU HỆ THỐNG', hãy dùng nó để trả lời chính xác thông tin (địa chỉ, website, lương...).
    - Nếu dữ liệu trống, hãy lịch sự thông báo chưa có dữ liệu chính thức.
    - Không bịa đặt thông tin nằm ngoài dữ liệu trên.
    """
    
    print(f"--- DEBUG CONTEXT SENT TO AI ---\n{db_context}\n-------------------------------")
    
    chat_session = start_chat_session()
    return get_chat_response(chat_session, prompt), "Mode: Clean RAG Vector"

def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session
    # Tìm job phù hợp với CV
    matched_jobs = search_vectors(cv_text, target_table="job_position", limit=3)
    prompt = f"CV: {cv_text[:3000]}\nJob gợi ý từ hệ thống: {matched_jobs}\nYêu cầu: {user_message}"
    chat_session = start_chat_session()
    return chat_session.send_message(prompt).text, "Mode: CV Reviewer"

def check_vector_coverage():
    """
    Kiểm tra và báo cáo tỷ lệ phần trăm dữ liệu đã được Vector hóa trong Database.
    """
    print("\n📊 [REPORT] KIỂM TRA ĐỘ PHỦ VECTOR TRONG DATABASE")
    print("-" * 60)
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # Danh sách các bảng cần kiểm tra
        targets = [
            ("semester", "semester_id"),
            ("major", "major_id"),
            ("company", "company_id"),
            ("ojtdocument", "ojtdocument_id"),
            ("job_position", "job_position_id"),
            ("job_description", "job_description_id"),
            ("finalreport", "finalreport_id"),
            ("companydocument", "companydocument_id"),
            ("User", "user_id")
        ]
        
        for table, id_col in targets:
            # Kiểm tra bảng có tồn tại cột embedding không
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
            cols = [r[0] for r in cur.fetchall()]
            
            if "embedding" not in cols:
                print(f"⚠️  Bảng [{table:.<18}]: Chưa có cột 'embedding'.")
                continue

            # Đếm tổng số dòng và số dòng thiếu embedding
            cur.execute(f'SELECT COUNT(*), COUNT(embedding) FROM "{table}"')
            total, has_vector = cur.fetchone()
            missing = total - has_vector
            
            percentage = (has_vector / total * 100) if total > 0 else 0
            
            status = "✅ OK" if missing == 0 and total > 0 else "❌ MISSING"
            if total == 0: status = "⚪ EMPTY"

            print(f"{status} [{table:.<18}]: {has_vector}/{total} dòng ({percentage:>6.1f}%) | Thiếu: {missing}")

        print("-" * 60)
        print("💡 Gợi ý: Nếu thấy dòng nào báo MISSING, hãy chạy sync_missing_embeddings().\n")
            
    except Exception as e:
        print(f"❌ Lỗi khi kiểm tra: {e}")
    finally:
        if conn: conn.close()