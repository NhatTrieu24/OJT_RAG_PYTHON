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

# ==================== 3. ĐỒNG BỘ VECTOR (PHẲNG HÓA DỮ LIỆU) ====================

def get_existing_columns(cur, table_name):
    try:
        cur.execute(f'SELECT * FROM "{table_name}" LIMIT 0')
        return [desc[0] for desc in cur.description]
    except: return []

import psycopg2
import time

def sync_missing_embeddings():
    print("🔄 [System] Bắt đầu quy trình Phẳng hóa & Đồng bộ Vector toàn diện...")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # --- 1. PHẲNG HÓA JOB_POSITION (Gộp Công ty + Kỳ học + Chuyên ngành) ---
        print("   ∟ Xử lý: job_position (Flattened: Company, Semester, Major)")
        sql_job = """
            SELECT jp.job_position_id, 
                   'Vị trí tuyển dụng: ' || COALESCE(jp.job_title, '') || 
                   '. Tại công ty: ' || COALESCE(c.name, 'N/A') || 
                   '. Yêu cầu: ' || COALESCE(jp.requirements, 'Không có') || 
                   '. Quyền lợi: ' || COALESCE(jp.benefit, 'Trao đổi thêm') ||
                   '. Địa điểm: ' || COALESCE(jp.location, 'Toàn quốc') || 
                   '. Dành cho kỳ: ' || COALESCE(s.name, 'N/A') ||
                   '. Thuộc ngành: ' || COALESCE(m.major_title, 'N/A') as full_text
            FROM job_position jp
            LEFT JOIN semester_company sc ON jp.semester_company_id = sc.semester_company_id
            LEFT JOIN company c ON sc.company_id = c.company_id
            LEFT JOIN semester s ON jp.semester_id = s.semester_id
            LEFT JOIN major m ON jp.major_id = m.major_id
            WHERE jp.embedding IS NULL;
        """
        process_batch_sync(cur, conn, sql_job, "job_position", "job_position_id")

        # --- 2. PHẲNG HÓA OJT_DOCUMENT (Gộp Kỳ học) ---
        print("   ∟ Xử lý: ojtdocument (Flattened: Semester)")
        sql_ojtdoc = """
            SELECT od.ojtdocument_id, 
                   'Tài liệu quy định OJT: ' || COALESCE(od.title, '') || 
                   '. Áp dụng cho kỳ học: ' || COALESCE(s.name, 'Chung') as full_text
            FROM ojtdocument od
            LEFT JOIN semester s ON od.semester_id = s.semester_id
            WHERE od.embedding IS NULL;
        """
        process_batch_sync(cur, conn, sql_ojtdoc, "ojtdocument", "ojtdocument_id")

        # --- 3. PHẲNG HÓA USER (Gộp Chuyên ngành + Công ty thực tập) ---
        print("   ∟ Xử lý: User (Flattened: Major, Company)")
        sql_user = """
            SELECT u.user_id, 
                   'Sinh viên: ' || COALESCE(u.fullname, '') || 
                   '. MSSV: ' || COALESCE(u.student_code, 'N/A') ||
                   '. Ngành học: ' || COALESCE(m.major_title, 'N/A') || 
                   '. Công ty đang thực tập: ' || COALESCE(c.name, 'Chưa đi thực tập') as full_text
            FROM "User" u
            LEFT JOIN major m ON u.major_id = m.major_id
            LEFT JOIN company c ON u.company_id = c.company_id
            WHERE u.embedding IS NULL;
        """
        process_batch_sync(cur, conn, sql_user, "User", "user_id")

        # --- 4. PHẲNG HÓA FINALREPORT (Gộp SV + Job + Kỳ học) ---
        print("   ∟ Xử lý: finalreport (Flattened: Student, Job, Semester)")
        sql_report = """
            SELECT fr.finalreport_id,
                   'Báo cáo cuối kỳ của SV: ' || COALESCE(u.fullname, '') ||
                   '. Vị trí thực tập: ' || COALESCE(jp.job_title, '') ||
                   '. Kỳ học: ' || COALESCE(s.name, '') ||
                   '. Nội dung báo cáo: ' || COALESCE(fr.student_report_text, '') ||
                   '. Nhận xét công ty: ' || COALESCE(fr.company_feedback, '') as full_text
            FROM finalreport fr
            LEFT JOIN "User" u ON fr.user_id = u.user_id
            LEFT JOIN job_position jp ON fr.job_position_id = jp.job_position_id
            LEFT JOIN semester s ON fr.semester_id = s.semester_id
            WHERE fr.embedding IS NULL;
        """
        process_batch_sync(cur, conn, sql_report, "finalreport", "finalreport_id")

        # --- 5. CÁC BẢNG DANH MỤC (Company, Major, Semester) ---
        targets = [
            ("company", "name", "company_id"),
            ("semester", "name", "semester_id"),
            ("major", "major_title", "major_id"),
            ("companydocument", "title", "companydocument_id")
        ]
        for table, col, id_col in targets:
            sql_simple = f'SELECT "{id_col}", "{col}" FROM "{table}" WHERE embedding IS NULL AND "{col}" IS NOT NULL'
            process_batch_sync(cur, conn, sql_simple, table, id_col)

        print("🎉 [System] Hoàn tất phẳng hóa toàn bộ Database.")
    except Exception as e:
        print(f"❌ Lỗi đồng bộ: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def process_batch_sync(cur, conn, sql, table_name, id_col):
    cur.execute(sql)
    rows = cur.fetchall()
    if not rows: return
    
    print(f"      -> Cập nhật {len(rows)} dòng cho [{table_name}]")
    ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]
    
    # Chia nhỏ batch 50 để tránh lỗi Rate Limit của Vertex AI
    batch_size = 50
    for i in range(0, len(rows), batch_size):
        sub_ids = ids[i : i + batch_size]
        sub_texts = texts[i : i + batch_size]
        try:
            vectors = get_embeddings_batch(sub_texts)
            for rid, vec in zip(sub_ids, vectors):
                cur.execute(f'UPDATE "{table_name}" SET embedding = %s WHERE "{id_col}" = %s', (vec, rid))
            conn.commit()
        except Exception as e:
            print(f"      ⚠️ Lỗi batch tại {table_name}: {e}")
            conn.rollback()
# ==================== 4. SEARCH & RAG ====================

def search_vectors(question, target_table="auto", limit=5):
    query_vector = get_query_embedding(question)
    if not query_vector: return ""

    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        q_lower = question.lower()
        
        # 1. Nhận diện bảng mục tiêu
        mapping = {
            "semester": ["kỳ", "học kỳ", "spring", "summer", "fall", "2025"],
            "company": ["địa chỉ", "website", "liên hệ", "văn phòng"],
            "ojtdocument": ["ojt", "quy định", "hướng dẫn", "tài liệu"],
            "job_position": ["việc làm", "tuyển dụng", "job", "lương", "vị trí", "momo", "fpt"]
        }

        tables_to_search = []
        for tbl, keywords in mapping.items():
            if any(k in q_lower for k in keywords):
                tables_to_search.append(tbl)
        
        if not tables_to_search:
            tables_to_search = ["job_position", "company", "ojtdocument"]

        final_results = []
        for table in set(tables_to_search):
            # --- LOGIC ĐẶC BIỆT CHO JOB_POSITION: JOIN BẮC CẦU ---
            if table == "job_position":
                sql = """
                    SELECT 
                        jp.job_title, jp.location, jp.salary_range, jp.requirements,
                        c.name as company_name,
                        1 - (jp.embedding <=> %s::vector) as sim
                    FROM job_position jp
                    LEFT JOIN semester_company sc ON jp.semester_company_id = sc.semester_company_id
                    LEFT JOIN company c ON sc.company_id = c.company_id
                    WHERE jp.embedding IS NULL OR jp.embedding IS NOT NULL 
                    ORDER BY jp.embedding <=> %s::vector LIMIT %s
                """
                cur.execute(sql, (query_vector, query_vector, limit))
                for r in cur.fetchall():
                    if r[-1] > 0.20: # Ngưỡng thấp để bắt được dữ liệu liên quan MoMo
                        final_results.append(
                            f"[JOB] Vị trí: {r[0]} | Công ty: {r[4]} | Địa điểm: {r[1]} | "
                            f"Lương: {r[2]} | Yêu cầu: {r[3]}"
                        )
            
            # --- LOGIC CHO CÁC BẢNG KHÁC (GIỮ NGUYÊN) ---
            else:
                cols = get_existing_columns(cur, table)
                if "embedding" not in cols: continue
                d_map = {
                    "semester": ["name", "start_date"],
                    "company": ["name", "address", "website"],
                    "ojtdocument": ["title", "file_url"]
                }
                s_cols = [c for c in d_map.get(table, []) if c in cols] or cols[:2]
                cols_sql = ", ".join([f'"{c}"' for c in s_cols])

                sql = f'SELECT {cols_sql}, 1 - (embedding <=> %s::vector) FROM "{table}" ORDER BY embedding <=> %s::vector LIMIT 3'
                cur.execute(sql, (query_vector, query_vector))
                for r in cur.fetchall():
                    if r[-1] > 0.30:
                        content = " | ".join([f"{s_cols[j]}: {r[j]}" for j in range(len(s_cols)) if r[j]])
                        final_results.append(f"[{table.upper()}] {content}")

        context_str = "\n".join(final_results)
        print(f"🔍 [Search] Context bốc được: \n{context_str[:500]}...")
        return context_str

    except Exception as e:
        print(f"❌ Search Error: {e}")
        return ""
    finally:
        if conn: conn.close()

# ==================== 5. HÀM REVIEW CV & AGENT ====================

def run_agent(question: str, file_content: str = None):
    from rag_core import start_chat_session, get_chat_response
    db_context = search_vectors(question)
    prompt = f"DỮ LIỆU HỆ THỐNG:\n{db_context}\n\nCÂU HỎI: {question}"
    return get_chat_response(start_chat_session(), prompt), "Mode: Clean RAG Vector"

def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session
    # Khi tìm job cho CV, Vector Search sẽ tự khớp các Job đã được phẳng hóa với tên công ty
    matched_jobs = search_vectors(cv_text, target_table="job_position", limit=3)
    prompt = f"CV: {cv_text[:3000]}\nJob gợi ý: {matched_jobs}\nYêu cầu: {user_message}"
    chat_session = start_chat_session()
    return chat_session.send_message(prompt).text, "Mode: CV Reviewer"

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