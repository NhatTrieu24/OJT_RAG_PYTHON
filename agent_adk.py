import os
import time
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel
from tenacity import retry, stop_after_attempt, wait_exponential

# ==================== 1. CẤU HÌNH AUTHENTICATION ====================
render_secret = "/etc/secrets/GCP_SERVICE_ACCOUNT_JSON"
local_key = "rag-service-account.json" 

if os.path.exists(render_secret): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = render_secret
    print("🔑 [Auth] Sử dụng Key từ Render Secrets.")
elif os.path.exists(local_key): 
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(local_key)
    print("🔑 [Auth] Sử dụng Key từ file Local.")
else:
    print("❌ [Auth] Không tìm thấy Service Account Key!")

PROJECT_ID = os.getenv("PROJECT_ID", "reflecting-surf-477600-p4")
LOCATION = os.getenv("LOCATION", "us-west1")
DB_DSN = os.getenv("DB_DSN", "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway")

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

# ==================== 3. ĐỒNG BỘ VECTOR (SMART SYNC & FORCE RESET) ====================

def sync_all_data(force_reset=False):
    """
    Hàm đồng bộ thông minh tích hợp Reset.
    - force_reset=True: Xóa sạch Vector cũ để tạo lại theo cấu trúc phẳng hóa mới.
    """
    print(f"🔄 [System] Bắt đầu đồng bộ {'(LÀM MỚI TOÀN BỘ)' if force_reset else ''}...")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()

        if force_reset:
            print("⚠️ [Reset] Đang xóa sạch dữ liệu Vector và Index cũ trên tất cả bảng cốt lõi...")
            tables_to_reset = [
                "job_position", "company", "semester", "User", "major", 
                "ojtdocument", "job_description", "companydocument", "job_title_overview"
            ]
            for table in tables_to_reset:
                try:
                    cur.execute(f'UPDATE "{table}" SET embedding = NULL, last_content_indexed = NULL;')
                except: pass
            conn.commit()

        scenarios = [
            {
                "table": "job_position",
                "id_col": "job_position_id",
                "sql": """
                    SELECT jp.job_position_id as id, 
                           'THÔNG TIN TUYỂN DỤNG: Vị trí ' || COALESCE(jp.job_title, '') || 
                           '. Tại công ty: ' || COALESCE(c.name, 'N/A') || 
                           '. Mức lương: ' || COALESCE(jp.salary_range, 'Thỏa thuận') || 
                           '. Yêu cầu: ' || COALESCE(jp.requirements, 'Không có') || 
                           '. Địa điểm: ' || COALESCE(jp.location, 'N/A') as text
                    FROM job_position jp
                    LEFT JOIN semester_company sc ON jp.semester_company_id = sc.semester_company_id
                    LEFT JOIN company c ON sc.company_id = c.company_id
                """
            },
            {
                "table": "ojtdocument",
                "id_col": "ojtdocument_id",
                "sql": "SELECT ojtdocument_id as id, 'TÀI LIỆU OJT: ' || COALESCE(title, '') || '. Link tải: ' || COALESCE(file_url, '') as text FROM ojtdocument"
            },
            {
                "table": "semester",
                "id_col": "semester_id",
                "sql": "SELECT semester_id as id, 'LỊCH KỲ HỌC: ' || COALESCE(name, '') || '. Bắt đầu: ' || COALESCE(start_date::text, '') || '. Kết thúc: ' || COALESCE(end_date::text, '') as text FROM semester"
            },
            {
                "table": "User",
                "id_col": "user_id",
                "sql": "SELECT user_id as id, 'HỒ SƠ: ' || COALESCE(fullname, '') || '. MSSV: ' || COALESCE(student_code, 'N/A') || '. Vai trò: ' || COALESCE(role, '') as text FROM \"User\""
            },
            {
                "table": "company",
                "id_col": "company_id",
                "sql": "SELECT company_id as id, 'CÔNG TY: ' || COALESCE(name, '') || '. Địa chỉ: ' || COALESCE(address, '') || '. Web: ' || COALESCE(website, '') as text FROM company"
            },
            {
                "table": "major",
                "id_col": "major_id",
                "sql": "SELECT major_id as id, 'NGÀNH HỌC: ' || COALESCE(major_title, '') || '. Mô tả: ' || COALESCE(description, '') as text FROM major"
            }
        ]

        for sc in scenarios:
            table = sc['table']
            id_col = sc['id_col']
            
            sync_query = f"""
                WITH latest_text AS ({sc['sql']})
                SELECT lt.id, lt.text 
                FROM latest_text lt
                LEFT JOIN "{table}" t ON lt.id = t."{id_col}"
                WHERE t.embedding IS NULL 
                   OR t.last_content_indexed IS NULL 
                   OR lt.text <> t.last_content_indexed;
            """
            cur.execute(sync_query)
            rows = cur.fetchall()
            
            if rows:
                print(f"   📦 Bảng [{table}]: Cập nhật {len(rows)} dòng.")
                process_batch_sync(cur, conn, rows, table, id_col)
            else:
                print(f"   ✅ Bảng [{table}]: Đã đồng bộ.")

        print("🎉 [System] Toàn bộ Database đã ở trạng thái mới nhất.")
    except Exception as e:
        print(f"❌ Lỗi: {e}"); 
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def process_batch_sync(cur, conn, rows, table_name, id_col):
    batch_size = 50
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]
        vectors = get_embeddings_batch(texts)
        if vectors:
            for idx, vec in enumerate(vectors):
                cur.execute(f'UPDATE "{table_name}" SET embedding = %s, last_content_indexed = %s WHERE "{id_col}" = %s', 
                            (vec, texts[idx], ids[idx]))
            conn.commit()

# ==================== 4. SEARCH & RAG ====================

def search_vectors(question, limit=10):
    query_vector = get_query_embedding(question)
    if not query_vector: return ""
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        q_lower = question.lower()
        threshold = 0.18 if any(k in q_lower for k in ["lương", "ngày", "mssv", "link", "url"]) else 0.25

        tables = ["job_position", "company", "ojtdocument", "semester", "major", "User"]
        final_results = []

        for table in tables:
            sql = f'SELECT last_content_indexed, 1 - (embedding <=> %s::vector) FROM "{table}" WHERE embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT 5'
            cur.execute(sql, (query_vector, query_vector))
            for r in cur.fetchall():
                if r[1] > threshold:
                    final_results.append(f"[{table.upper()}] {r[0]}")
        return "\n".join(final_results)
    finally:
        if conn: conn.close()

def run_agent(question: str, file_content: str = None):
    from rag_core import start_chat_session, get_chat_response
    # Nhờ AI sửa lỗi chính tả và bung viết tắt (Query Expansion)
    
    refine_prompt = f"""
    Bạn là chuyên gia xử lý ngôn ngữ. Nhiệm vụ của bạn là chuẩn hóa câu hỏi của sinh viên.
    - Bung viết tắt: tt -> thực tập, sv -> sinh viên, mssv -> mã số sinh viên, cty -> công ty, nv -> nhân viên.
    - Giữ nguyên tên riêng/công ty: MoMo, FPT, Viettel, VNG, Shopee...
    - Sửa lỗi chính tả và thêm dấu nếu thiếu.
    - Nếu có từ 'mô mô', hãy hiểu đó là công ty 'MoMo'.
    
    Câu hỏi gốc: "{question}"
    Câu hỏi đã chuẩn hóa (chỉ trả về nội dung câu):"""
    
    refine_session = start_chat_session()
    clean_question = refine_session.send_message(refine_prompt).text.strip()
    #-----------------------------------------------------------
    print(f"🔍 [Refine] Gốc: {question} -> Đã sửa: {clean_question}")
    db_context = search_vectors(clean_question)
    prompt = f"DỮ LIỆU HỆ THỐNG:\n{db_context}\n\nCÂU HỎI: {clean_question}\n\nYÊU CẦU: CHỈ dùng dữ liệu trên. Trả lời chính xác Lương/Ngày/MSSV/URL."
    print(f"--- DEBUG CONTEXT ---\n{db_context}")
    return get_chat_response(start_chat_session(), prompt), "Mode: Smart Deep RAG"
   
def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session
    
    # Sử dụng nội dung CV để tìm kiếm các công việc phù hợp nhất trong database
    # Vì cv_text thường dài, search_vectors sẽ bốc ra những job có yêu cầu kỹ năng tương đồng
    matched_jobs = search_vectors(cv_text, limit=5) 
    
    prompt = f"""
    HỒ SƠ SINH VIÊN (CV): 
    {cv_text[:3500]} 
    
    CÁC VỊ TRÍ TUYỂN DỤNG VÀ QUY ĐỊNH OJT TÌM THẤY: 
    {matched_jobs}
    
    YÊU CẦU CỦA NGƯỜI DÙNG: {user_message}
    
    HƯỚNG DẪN TRẢ LỜI:
    1. Phân tích sự phù hợp giữa kỹ năng trong CV và yêu cầu của các Job.
    2. Đánh giá sinh viên có đủ điều kiện đi OJT theo quy định của trường không.
    3. Trả lời bằng Tiếng Việt, trình bày rõ ràng, chuyên nghiệp. 
    4. Nếu đủ điều kiện, hãy gợi ý vị trí khớp nhất. Nếu chưa, hãy chỉ ra kỹ năng cần bổ sung.
    """
    
    print(f"--- [Mode: CV Review] Đã bốc {len(matched_jobs)} đoạn ngữ cảnh cho CV ---")
    chat_session = start_chat_session()
    return chat_session.send_message(prompt).text, "Mode: CV Reviewer Intelligence"
def check_vector_coverage():
    print("\n📊 [REPORT] KIỂM TRA ĐỘ PHỦ VECTOR")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        targets = [("semester", "semester_id"), ("company", "company_id"), ("job_position", "job_position_id"), ("ojtdocument", "ojtdocument_id")]
        for table, id_col in targets:
            cur.execute(f'SELECT COUNT(*), COUNT(embedding) FROM "{table}"')
            total, has_vec = cur.fetchone()
            print(f"[{table}]: {has_vec}/{total} dòng.")
    except Exception as e: print(f"Lỗi: {e}")
    finally:
        if conn: conn.close()