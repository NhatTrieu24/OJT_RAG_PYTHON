import os
import time
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel
from tenacity import retry, stop_after_attempt, wait_exponential
import io
import requests
import pdfplumber
import re
import docx  # Thư viện đọc file Word (.docx)

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

# ==================== 2. HÀM BỔ TRỢ (DRIVE & EMBEDDING) ====================

def get_text_from_drive(file_url):
    if not file_url or "drive.google.com" not in file_url: return ""
    try:
        # Tách ID file
        file_id = re.search(r'[-\w]{25,}', file_url).group()
        # Sử dụng link download trực tiếp của Google
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        
        response = requests.get(download_url, timeout=20, allow_redirects=True)
        if response.status_code == 200:
            stream = io.BytesIO(response.content)
            # Thử đọc PDF
            try:
                with pdfplumber.open(stream) as pdf:
                    return " ".join([p.extract_text() for p in pdf.pages[:5] if p.extract_text()])
            except:
                # Nếu không phải PDF, thử đọc Word
                stream.seek(0)
                doc = docx.Document(stream)
                return " ".join([p.text for p in doc.paragraphs])
    except Exception as e:
        print(f"❌ Lỗi đọc link: {e}")
    return ""



@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=5, min=10, max=120))
def get_embeddings_batch(texts):
    if not embedding_model or not texts: return []
    # Làm sạch text để tối ưu TPM (Tokens Per Minute)
    clean_texts = [str(t).replace("\n", " ").strip()[:2500] for t in texts if t]
    if not clean_texts: return []
    try:
        embeddings = embedding_model.get_embeddings(clean_texts)
        return [e.values for e in embeddings]
    except Exception as e:
        print(f"⚠️ API Warning: {e}. Đang đợi hồi Quota...")
        raise e

def get_query_embedding(text):
    res = get_embeddings_batch([text])
    return res[0] if res else None

# ==================== 3. ĐỒNG BỘ VECTOR THÔNG MINH (BATCH MODE) ====================

def sync_all_data(force_reset=False):
    print(f"🔄 [System] Bắt đầu đồng bộ thông minh (BATCH MODE - PDF & Word)...")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()

        if force_reset:
            print("⚠️ [Reset] Đang Reset toàn bộ bộ nhớ Vector...")
            tables = ["job_position", "company", "semester", "User", "major", "ojtdocument"]
            for t in tables:
                cur.execute(f'UPDATE "{t}" SET embedding = NULL, last_content_indexed = NULL;')
            conn.commit()

        scenarios = [
            {
                "table": "job_position",
                "id_col": "job_position_id",
                "sql": """
                    SELECT jp.job_position_id, 
                           'VỊ TRÍ: ' || COALESCE(jp.job_title, '') || '. CÔNG TY: ' || COALESCE(c.name, 'N/A') || 
                           '. YÊU CẦU: ' || COALESCE(jp.requirements, 'Không có') as text
                    FROM job_position jp
                    LEFT JOIN semester_company sc ON jp.semester_company_id = sc.semester_company_id
                    LEFT JOIN company c ON sc.company_id = c.company_id
                """
            },
            {
                "table": "ojtdocument",
                "id_col": "ojtdocument_id",
                "sql": "SELECT ojtdocument_id, title, file_url FROM ojtdocument"
            },
            {
                "table": "semester",
                "id_col": "semester_id",
                "sql": "SELECT semester_id, 'LỊCH KỲ HỌC: ' || COALESCE(name, '') as text FROM semester"
            },
            {
                "table": "User",
                "id_col": "user_id",
                "sql": "SELECT user_id, 'HỒ SƠ: ' || COALESCE(fullname, '') as text FROM \"User\""
            },
            {
                "table": "company",
                "id_col": "company_id",
                "sql": "SELECT company_id, 'CÔNG TY: ' || COALESCE(name, '') as text FROM company"
            },
            {
                "table": "major",
                "id_col": "major_id",
                "sql": "SELECT major_id, 'NGÀNH HỌC: ' || COALESCE(major_title, '') as text FROM major"
            }
        ]

        for sc in scenarios:
            table = sc['table']
            cur.execute(f"""
                WITH latest AS ({sc['sql']})
                SELECT l.* FROM latest l
                LEFT JOIN "{table}" t ON l.{sc['id_col']} = t."{sc['id_col']}"
                WHERE t.embedding IS NULL OR t.last_content_indexed IS NULL;
            """)
            rows = cur.fetchall()

            if rows:
                print(f"📦 Bảng [{table}]: Phát hiện {len(rows)} dòng cần xử lý.")
                batch_texts, batch_ids = [], []

                for r in rows:
                    rid = r[0]
                    if table == "ojtdocument":
                        title = r[1] if len(r) > 1 else "Tài liệu"
                        url = r[2] if len(r) > 2 else ""
                        print(f"  📥 Trích xuất PDF/Word: {title}")
                        content = get_text_from_drive(url)
                        final_text = f"TÀI LIỆU OJT: {title}. NỘI DUNG: {content}. Link: {url}"
                    else:
                        final_text = r[1]
                    
                    batch_texts.append(final_text)
                    batch_ids.append(rid)

                # Batch 5 dòng để tối ưu Quota
                sub_batch_size = 5
                for i in range(0, len(batch_texts), sub_batch_size):
                    s_texts = batch_texts[i : i + sub_batch_size]
                    s_ids = batch_ids[i : i + sub_batch_size]
                    
                    print(f"📡 Đang gửi batch {i//sub_batch_size + 1} lên Vertex AI...")
                    vectors = get_embeddings_batch(s_texts)
                    
                    if vectors:
                        for idx, vec in enumerate(vectors):
                            cur.execute(f'UPDATE "{table}" SET embedding = %s, last_content_indexed = %s WHERE "{sc["id_col"]}" = %s', 
                                       (vec, s_texts[idx], s_ids[idx]))
                        conn.commit()
                        print(f"  ✅ Đã lưu {len(vectors)} dòng. Nghỉ 5s...")
                        time.sleep(5)
            else:
                print(f"✅ Bảng [{table}]: Đã đồng bộ.")

        print("🎉 [System] Hoàn tất đồng bộ toàn bộ dữ liệu.")
    except Exception as e:
        print(f"❌ Lỗi Sync: {e}"); conn.rollback() if conn else None
    finally:
        if conn: conn.close()

# ==================== 4. CORE RAG LOGIC ====================

def search_vectors(question, limit=7):
    query_vector = get_query_embedding(question)
    if not query_vector: return ""
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        results = []
        for t in ["ojtdocument", "job_position", "company", "semester"]:
            cur.execute(f'SELECT last_content_indexed, 1 - (embedding <=> %s::vector) as score FROM "{t}" WHERE embedding IS NOT NULL ORDER BY score DESC LIMIT 3', (query_vector,))
            for r in cur.fetchall():
                if r[1] > 0.18: 
                    results.append(f"[{t.upper()}] {r[0]}")
        return "\n".join(results)
    finally:
        if conn: conn.close()

def run_agent(question: str, file_content: str = None):
    from rag_core import start_chat_session, get_chat_response
    import re
    import psycopg2
    
    clean_question = question
    # 1. AI Refiner: Bung viết tắt (tt, mssv) nhưng giữ nguyên tên riêng
    abbr_patterns = [r'\btt\b', r'\bmssv\b', r'\bmô mô\b']
    if len(question.split()) < 5 or any(re.search(p, question.lower()) for p in abbr_patterns):
        refine_p = f"Chuẩn hóa câu hỏi: '{question}'. Bung viết tắt (tt=thực tập, mssv=mã số sinh viên). Giữ nguyên tên riêng. Chỉ trả về câu đã sửa."
        try:
            clean_question = start_chat_session().send_message(refine_p).text.strip()
            print(f"🔍 [Refine] {question} -> {clean_question}")
        except:
            clean_question = question

    # 2. Lấy Context từ Vector Search (Truy vấn đa bảng: job, company, user...)
    db_context = search_vectors(clean_question)
    
    # 3. Xử lý đọc nội dung Link Drive trực tiếp (Nếu câu hỏi liên quan đến tài liệu OJT)
    drive_content = ""
    target_link = ""
    # Tìm xem trong db_context có chứa link ojtdocument không
    if "[OJTDOCUMENT]" in db_context.upper():
        # Trích xuất link drive từ context bằng Regex
        link_match = re.search(r'https://drive\.google\.com/[^\s]+', db_context)
        if link_match:
            target_link = link_match.group(0)
            print(f"📂 AI đang truy cập trực tiếp link để lấy nội dung chi tiết: {target_link}")
            drive_content = get_text_from_drive(target_link)

    # 4. Xây dựng Prompt tổng hợp
    final_prompt = f"""
    DỮ LIỆU HỆ THỐNG (BẮT BUỘC SỬ DỤNG):
    {db_context if db_context else "Không tìm thấy dữ liệu liên quan trong DB."}
    
    NỘI DUNG ĐỌC TRỰC TIẾP TỪ LINK DRIVE (NẾU CÓ):
    {drive_content if drive_content else "Không có nội dung bổ sung từ link."}
    
    FILE NGƯỜI DÙNG TẢI LÊN (NẾU CÓ): 
    {file_content if file_content else "N/A"}
    
    CÂU HỎI: {clean_question}
    
    YÊU CẦU:
    - ƯU TIÊN sử dụng 'NỘI DUNG ĐỌC TRỰC TIẾP TỪ LINK DRIVE' để trả lời chi tiết các quy định OJT.
    - Sử dụng 'DỮ LIỆU HỆ THỐNG' để trả lời chính xác thông tin công ty, địa chỉ, lương, hoặc thông tin sinh viên.
    - Trình bày câu trả lời chuyên nghiệp, rõ ràng từng ý.
    - PHẦN QUAN TRỌNG VỀ LINK: 
       - Cuối câu trả lời, chỉ hiển thị một danh sách duy nhất các 'Link tài liệu tham khảo'.
       - Tuyệt đối KHÔNG liệt kê lặp lại cùng một đường link.
       - Nếu Link từ dữ liệu hệ thống và Link từ nội dung trực tiếp là một, chỉ được hiển thị 1 lần duy nhất.
    """
    
    print(f"--- DEBUG CONTEXT SENT TO AI ---\n{db_context}\n-------------------------------")
    
    chat_session = start_chat_session()
    return get_chat_response(chat_session, final_prompt), "Mode: Hybrid Real-time RAG"


def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session
    context = search_vectors(cv_text)
    prompt = f"CV SINH VIÊN: {cv_text[:3000]}\n\nNGỮ CẢNH HỆ THỐNG: {context}\n\nYÊU CẦU: {user_message}\n\nHƯỚNG DẪN: Đánh giá độ phù hợp CV với Job và Quy định OJT."
    return start_chat_session().send_message(prompt).text, "Mode: CV Reviewer Intelligence"

def check_vector_coverage():
    conn = psycopg2.connect(dsn=DB_DSN)
    cur = conn.cursor()
    for t in ["job_position", "ojtdocument", "User", "company"]:
        cur.execute(f'SELECT COUNT(*), COUNT(embedding) FROM "{t}"')
        total, has_v = cur.fetchone()
        print(f"📊 {t}: {has_v}/{total} vectors.")
    conn.close()