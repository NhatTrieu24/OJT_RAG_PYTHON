import os
import re
import io
import time
import requests
import psycopg2
from psycopg2 import pool
import pdfplumber
import docx
from contextlib import contextmanager
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_fixed

# --- THƯ VIỆN AI LOCAL (QUAN TRỌNG) ---
from sentence_transformers import SentenceTransformer

load_dotenv()

# ==================== 1. CẤU HÌNH HỆ THỐNG ====================

# 1.1 Cấu hình Database & Connection Pool
LOCAL_DB_URL = "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway"
DB_DSN = os.environ.get("DB_DSN", LOCAL_DB_URL)

db_pool = None
try:
    # Tạo bể kết nối (Min 1, Max 10) để tránh mở lại connection liên tục
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, dsn=DB_DSN)
    print("✅ [DB] Connection Pool initialized.")
except Exception as e:
    print(f"❌ [DB] Pool Error: {e}")

# 1.2 Cấu hình AI Local (Embedding)
# Dùng model MiniLM: Nhanh, Nhẹ (300MB RAM), Vector size 384
print("⏳ [AI Local] Đang tải Model Embedding (MiniLM)...")
try:
    local_embedder = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
    print("✅ [AI Local] Model đã sẵn sàng!")
except Exception as e:
    print(f"❌ [AI Local] Load Model Error: {e}")
    local_embedder = None

# ==================== 2. TỪ ĐIỂN & HÀM BỔ TRỢ ====================

# Từ điển viết tắt (Regex) - Nhanh hơn gọi AI gấp 1000 lần
ABBREVIATIONS = {
    r"\btt\b": "thực tập",
    r"\bojt\b": "thực tập doanh nghiệp",
    r"\bmssv\b": "mã số sinh viên",
    r"\bcv\b": "hồ sơ xin việc",
    r"\bcty\b": "công ty",
    r"\bdn\b": "doanh nghiệp",
    r"\bsem\b": "học kỳ",
    r"\bjob\b": "việc làm",
    r"\bluong\b": "mức lương",
    r"\bhcm\b": "TP.HCM",
    r"\bhn\b": "Hà Nội"
}

def quick_process_text(text):
    """Chuẩn hóa text siêu tốc bằng Regex"""
    if not text: return ""
    text = text.lower().strip()
    for pattern, replacement in ABBREVIATIONS.items():
        text = re.sub(pattern, replacement, text)
    return re.sub(r'\s+', ' ', text)

@contextmanager
def get_db_connection():
    """Lấy kết nối từ Pool an toàn"""
    conn = None
    try:
        if db_pool:
            conn = db_pool.getconn()
            yield conn
        else:
            conn = psycopg2.connect(dsn=DB_DSN)
            yield conn
    except Exception as e:
        print(f"❌ DB Error: {e}")
        raise e
    finally:
        if conn and db_pool: db_pool.putconn(conn)
        elif conn: conn.close()

def get_text_from_drive(file_url):
    """Tải và đọc nội dung file PDF/Word từ Google Drive"""
    if not file_url or "drive.google.com" not in file_url: return ""
    try:
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', file_url) or re.search(r'id=([a-zA-Z0-9_-]+)', file_url)
        if not match: return ""
        url = f"https://drive.google.com/uc?export=download&id={match.group(1)}"
        
        # Timeout 10s để không treo server
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            stream = io.BytesIO(res.content)
            try:
                # Ưu tiên đọc PDF
                with pdfplumber.open(stream) as pdf:
                    return " ".join([p.extract_text() or "" for p in pdf.pages[:5]])
            except: 
                # Fallback sang Word
                stream.seek(0)
                doc = docx.Document(stream)
                return " ".join([p.text for p in doc.paragraphs])
    except: pass
    return ""

# ==================== 3. HÀM VECTOR LOCAL (KHÔNG TỐN QUOTA) ====================

def get_embeddings_batch(texts):
    """Tạo Vector bằng CPU Server (Free & Fast)"""
    if not local_embedder or not texts: return []
    # Cắt ngắn text để tránh lỗi model limit
    clean_texts = [str(t).replace("\n", " ").strip()[:1000] for t in texts if t]
    try:
        # encode trả về numpy array -> cần chuyển thành list
        embeddings = local_embedder.encode(clean_texts)
        return embeddings.tolist()
    except Exception as e:
        print(f"⚠️ Local Embed Error: {e}")
        return []

def get_query_embedding(text):
    """Tạo vector cho 1 câu hỏi"""
    try:
        embedding = local_embedder.encode(text)
        return embedding.tolist()
    except: return None

# ==================== 4. SEARCH ENGINE (TỐI ƯU HÓA) ====================

def search_vectors(question):
    t0 = time.time()
    
    # 1. Tạo vector câu hỏi (Local)
    query_vector = get_query_embedding(question)
    if not query_vector: return ""
    
    results = []
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 2. Query tối ưu (Gộp 9 bảng)
                # Lưu ý: DB Vector cột phải là vector(384)
                sql_query = """
                    (SELECT 'TÀI LIỆU', last_content_indexed, (embedding <=> %s::vector) as d FROM ojtdocument WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 3)
                    UNION ALL
                    (SELECT 'VIỆC LÀM', last_content_indexed, (embedding <=> %s::vector) as d FROM job_position WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 4)
                    UNION ALL
                    (SELECT 'DOANH NGHIỆP', last_content_indexed, (embedding <=> %s::vector) as d FROM company WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 2)
                    UNION ALL
                    (SELECT 'HỒ SƠ SV', last_content_indexed, (embedding <=> %s::vector) as d FROM "User" WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 2)
                    UNION ALL
                    (SELECT 'NGÀNH HỌC', last_content_indexed, (embedding <=> %s::vector) as d FROM major WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 2)
                    UNION ALL
                    (SELECT 'HỌC KỲ', last_content_indexed, (embedding <=> %s::vector) as d FROM semester WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 1)
                    UNION ALL
                    (SELECT 'DOC CÔNG TY', last_content_indexed, (embedding <=> %s::vector) as d FROM companydocument WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 2)
                    UNION ALL
                    (SELECT 'FEEDBACK', last_content_indexed, (embedding <=> %s::vector) as d FROM finalreport WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 2)
                    UNION ALL
                    (SELECT 'THỐNG KÊ', last_content_indexed, (embedding <=> %s::vector) as d FROM job_title_overview WHERE embedding IS NOT NULL ORDER BY d ASC LIMIT 2)
                    ORDER BY d ASC LIMIT 12
                """
                # Truyền tham số 9 lần cho 9 dấu %s
                params = (query_vector,) * 9 
                cur.execute(sql_query, params)
                
                for r in cur.fetchall():
                    # Lấy kết quả có khoảng cách < 0.65 (Càng nhỏ càng giống)
                    if r[2] < 0.85: 
                        results.append(f"[{r[0]}] {r[1]}")
                        
    except Exception as e:
        print(f"❌ Search Error: {e}")
    
    print(f"⚡ Local Search: {time.time() - t0:.3f}s")
    return "\n\n".join(results)

# ==================== 5. CORE RAG LOGIC ====================

def run_agent(question: str, file_content: str = None):
    # Import cục bộ để tránh lỗi circular import
    from rag_core import start_chat_session, get_chat_response
    
    t_start = time.time()
    
    # 1. Xử lý câu hỏi
    clean_question = quick_process_text(question)
    print(f"🧹 Input: '{question}' -> '{clean_question}'")
    
    # 2. Tìm kiếm Vector (Lấy link file và tóm tắt)
    # Lấy nhiều kết quả hơn chút để không bỏ sót
    db_context = search_vectors(clean_question) 
    
    # 3. KÍCH HOẠT ĐỌC FILE DRIVE TRỰC TIẾP (QUAN TRỌNG)
    # Nếu trong kết quả tìm kiếm có Link Drive, ta sẽ đọc nội dung chi tiết của nó
    realtime_file_content = ""
    source_link = ""
    
    if "drive.google.com" in db_context:
        # Tìm link đầu tiên xuất hiện trong context (thường là link liên quan nhất)
        link_match = re.search(r'https://drive\.google\.com/[^\s]+', db_context)
        if link_match:
            target_url = link_match.group(0).rstrip(").,") # Xóa dấu câu thừa nếu có
            print(f"🚀 [Real-time] Phát hiện tài liệu liên quan. Đang đọc chi tiết: {target_url}")
            
            # Gọi hàm tải và đọc file (Mất khoảng 1-2s nhưng chi tiết cực kỳ)
            realtime_file_content = get_text_from_drive(target_url)
            
            if realtime_file_content:
                source_link = target_url
                print(f"   ✅ Đã trích xuất được {len(realtime_file_content)} ký tự chi tiết.")
            else:
                print("   ⚠️ Không đọc được nội dung file (File ảnh hoặc lỗi quyền truy cập).")

    # 4. Tạo Prompt (Bơm dữ liệu chi tiết vào)
    final_prompt = f"""
    VAI TRÒ: Trợ lý tuyển dụng và đào tạo OJT chuyên nghiệp.

    DỮ LIỆU TÓM TẮT TỪ HỆ THỐNG:
    {db_context}
    
    --------------------------------------------------
    NỘI DUNG CHI TIẾT ĐẦY ĐỦ TỪ TÀI LIỆU (ƯU TIÊN DÙNG CÁI NÀY):
    {realtime_file_content if realtime_file_content else "Không đọc được nội dung chi tiết file."}
    --------------------------------------------------
    
    FILE NGƯỜI DÙNG TẢI LÊN (NẾU CÓ):
    {file_content if file_content else "N/A"}
    
    CÂU HỎI: {clean_question}
    
    YÊU CẦU TRẢ LỜI: 
    1. Dựa vào 'NỘI DUNG CHI TIẾT', hãy trích xuất toàn bộ thông tin quan trọng:
       - Giới thiệu công ty.
       - Vị trí tuyển dụng & Yêu cầu kỹ năng (Hard/Soft skills).
       - Quyền lợi (Lương, trợ cấp, môi trường).
       - Cách thức ứng tuyển (Email, Quy trình).
    2. Trình bày rõ ràng, gạch đầu dòng.
    3. Nếu có link tài liệu gốc ({source_link}), HÃY ĐỂ NÓ Ở CUỐI CÙNG để người dùng tham khảo.
    """
    
    # 5. Gửi cho AI
    try:
        chat_session = start_chat_session()
        answer = get_chat_response(chat_session, final_prompt)
    except Exception as e:
        answer = "⚠️ Hệ thống đang bận xử lý dữ liệu lớn. Vui lòng thử lại câu hỏi cụ thể hơn."
        print(f"❌ Chat Error: {e}")
    
    print(f"⏱️ Total Time: {time.time() - t_start:.3f}s")
    
    # Đánh dấu mode để dễ debug
    mode_label = "RAG Mode" if realtime_file_content else "RAG Fast Mode"
    
    return answer, mode_label

# ==================== 6. ĐỒNG BỘ DỮ LIỆU (SYNC ALL) ====================

def sync_all_data(force_reset=False):
    print(f"🔄 [Sync] Bắt đầu đồng bộ dữ liệu (Local Embedding)...")
    t_start = time.time()
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if force_reset:
                    print("⚠️ [Reset] Đang xóa vector cũ...")
                    tables = ["job_position", "company", "semester", "User", "major", "ojtdocument", "companydocument", "finalreport", "job_title_overview"]
                    for t in tables:
                        # Kiểm tra bảng tồn tại
                        cur.execute(f"SELECT to_regclass('public.\"{t}\"');")
                        if cur.fetchone()[0]:
                            cur.execute(f'UPDATE "{t}" SET embedding = NULL, last_content_indexed = NULL;')
                    conn.commit()

                # --- ĐỊNH NGHĨA KỊCH BẢN (GIỐNG CŨ NHƯNG CHẠY LOCAL) ---
                scenarios = [
                    # 1. Job
                    {"table": "job_position", "id": "job_position_id", "sql": """
                        SELECT jp.job_position_id, 'VỊ TRÍ: ' || COALESCE(jp.job_title, '') || '. CÔNG TY: ' || COALESCE(c.name, 'N/A') || '. LƯƠNG: ' || COALESCE(jp.salary_range, '') || '. MÔ TẢ: ' || COALESCE(jd.job_description, '') || '. YÊU CẦU: ' || COALESCE(jp.requirements, '') as text 
                        FROM job_position jp 
                        LEFT JOIN semester_company sc ON jp.semester_company_id = sc.semester_company_id
                        LEFT JOIN company c ON sc.company_id = c.company_id
                        LEFT JOIN job_description jd ON jp.job_position_id = jd.job_position_id
                    """},
                    # 2. User
                    {"table": "User", "id": "user_id", "sql": """
                        SELECT u.user_id, 'NGƯỜI DÙNG: ' || COALESCE(u.fullname, '') || '. MSSV: ' || COALESCE(u.student_code, '') || '. EMAIL: ' || COALESCE(u.email, '') || '. NGÀNH: ' || COALESCE(m.major_title, '') || '. CÔNG TY: ' || COALESCE(c.name, '') as text
                        FROM "User" u
                        LEFT JOIN major m ON u.major_id = m.major_id
                        LEFT JOIN company c ON u.company_id = c.company_id
                    """},
                    # 3. Docs (Có đọc file)
                    {"table": "ojtdocument", "id": "ojtdocument_id", "sql": "SELECT ojtdocument_id, title, file_url FROM ojtdocument"},
                    {"table": "companydocument", "id": "companydocument_id", "sql": """
                        SELECT cd.companydocument_id, 'DOC CÔNG TY: ' || COALESCE(c.name, '') || '. TÊN: ' || COALESCE(cd.title, '') as text, cd.file_url 
                        FROM companydocument cd LEFT JOIN semester_company sc ON cd.semester_company_id = sc.semester_company_id LEFT JOIN company c ON sc.company_id = c.company_id
                    """},
                    # 4. Các bảng đơn lẻ khác
                    {"table": "company", "id": "company_id", "sql": "SELECT company_id, 'CÔNG TY: ' || COALESCE(name, '') || '. ĐỊA CHỈ: ' || COALESCE(address, '') || '. EMAIL: ' || COALESCE(contact_email, '') as text FROM company"},
                    {"table": "semester", "id": "semester_id", "sql": "SELECT semester_id, 'HỌC KỲ: ' || COALESCE(name, '') || '. TỪ: ' || COALESCE(start_date::text, '') || ' ĐẾN: ' || COALESCE(end_date::text, '') as text FROM semester"},
                    {"table": "major", "id": "major_id", "sql": "SELECT major_id, 'NGÀNH: ' || COALESCE(major_title, '') || '. MÔ TẢ: ' || COALESCE(description, '') as text FROM major"},
                    {"table": "finalreport", "id": "finalreport_id", "sql": """
                         SELECT fr.finalreport_id, 'ĐÁNH GIÁ: SV ' || COALESCE(u.fullname, '') || ' TẠI ' || COALESCE(c.name, '') || '. ĐIỂM: ' || COALESCE(fr.company_rating::text, '0') || '. NHẬN XÉT: ' || COALESCE(fr.company_feedback, '') as text
                         FROM finalreport fr LEFT JOIN "User" u ON fr.user_id = u.user_id LEFT JOIN job_position jp ON fr.job_position_id = jp.job_position_id LEFT JOIN semester_company sc ON jp.semester_company_id = sc.semester_company_id LEFT JOIN company c ON sc.company_id = c.company_id
                    """},
                    {"table": "job_title_overview", "id": "job_title_id", "sql": "SELECT job_title_id, 'THỐNG KÊ VIỆC LÀM: ' || COALESCE(job_title, '') || '. SỐ LƯỢNG: ' || COALESCE(position_amount::text, '0') as text FROM job_title_overview"}
                ]

                # --- LOOP XỬ LÝ ---
                for sc in scenarios:
                    table = sc['table']
                    id_col = sc['id']
                    
                    cur.execute(f"SELECT to_regclass('public.\"{table}\"');")
                    if not cur.fetchone()[0]: continue

                    # Lấy dữ liệu chưa index
                    cur.execute(f"""
                        WITH source AS ({sc['sql']})
                        SELECT s.* FROM source s JOIN "{table}" t ON s.{id_col} = t."{id_col}"
                        WHERE t.embedding IS NULL OR t.last_content_indexed IS NULL
                    """)
                    rows = cur.fetchall()
                    if not rows: continue
                    
                    print(f"📦 [{table}] Xử lý {len(rows)} dòng mới.")
                    BATCH_SIZE = 10
                    
                    for i in range(0, len(rows), BATCH_SIZE):
                        batch = rows[i : i + BATCH_SIZE]
                        batch_texts, batch_ids = [], []

                        for r in batch:
                            rid = r[0]
                            # Xử lý File Drive
                            if table in ["ojtdocument", "companydocument"]:
                                title = r[1]
                                url = r[2] if len(r) > 2 else ""
                                content = ""
                                if "drive.google.com" in url:
                                    print(f"   📥 Đọc file: {title[:20]}...")
                                    content = get_text_from_drive(url)
                                final_text = f"{title}. CHI TIẾT: {content}. Link: {url}"
                            else:
                                final_text = r[1]
                            
                            batch_texts.append(final_text)
                            batch_ids.append(rid)

                        # Tạo Embedding LOCAL
                        vectors = get_embeddings_batch(batch_texts)
                        
                        # Lưu vào DB
                        if vectors:
                            for idx, vec in enumerate(vectors):
                                cur.execute(f'UPDATE "{table}" SET embedding = %s, last_content_indexed = %s WHERE "{id_col}" = %s', 
                                            (vec, batch_texts[idx], batch_ids[idx]))
                            conn.commit()
                            print(f"   ✅ Saved batch {i//BATCH_SIZE + 1} (Local Vector).")

        print(f"🎉 [Sync] Hoàn tất sau {time.time() - t_start:.2f}s.")
    except Exception as e:
        print(f"❌ Lỗi Sync: {e}")

# ==================== 7. CV REVIEW (NÂNG CẤP) ====================

def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session, get_chat_response
    
    # 1. Debug xem đã đọc được nội dung CV chưa
    print(f"📄 [CV Review] Đã đọc được: {len(cv_text)} ký tự từ CV.")
    if len(cv_text) < 100:
        return "⚠️ Lỗi: Không đọc được nội dung CV (File có thể là ảnh scan hoặc bị lỗi font). Vui lòng thử file PDF khác.", "CV Error"

    # 2. Tạo Search Query thông minh hơn
    # Thay vì tìm nguyên văn cả CV, ta trích xuất 500 ký tự đầu (thường chứa Tech Stack) 
    # và thêm từ khóa để ép Vector tìm về phía Job/Company thay vì tài liệu OJT.
    search_query = cv_text[:500] + " Tìm việc làm phù hợp cho ứng viên dựa trên kỹ năng công nghệ và kinh nghiệm làm việc."
    
    # 3. Tìm kiếm Job trong DB
    # (Lấy kết quả context từ hàm search_vectors có sẵn)
    context = search_vectors(search_query)
    
    # 4. Prompt "Match Maker" (So khớp ứng viên & Việc làm)
    prompt = f"""
    VAI TRÒ: Bạn là chuyên gia HR. Nhiệm vụ là ghép đôi ứng viên với công việc phù hợp nhất.

    DỮ LIỆU ĐẦU VÀO:
    ----------------
    1. HỒ SƠ ỨNG VIÊN (CV):
    {cv_text[:3000]} ... (lược bớt)
    
    2. DANH SÁCH VIỆC LÀM TRONG HỆ THỐNG (Database):
    {context}
    
    3. CÂU HỎI CỦA ỨNG VIÊN: "{user_message}"
    ----------------
    
    YÊU CẦU PHÂN TÍCH:
    1. BỎ QUA các tài liệu hướng dẫn OJT (File PDF quy định). Chỉ tập trung vào [VIỆC LÀM] hoặc [DOANH NGHIỆP].
    2. Phân tích kỹ năng cứng (Hard Skills) trong CV (VD: Java, React, Python, SQL...).
    3. So sánh với "Yêu cầu" (Requirements) của các Job tìm thấy.
    4. Đưa ra Top 3 công ty/vị trí phù hợp nhất.
    
    ĐỊNH DẠNG TRẢ LỜI (Bắt buộc):
    🎯 **Top 1: [Tên Vị Trí] - [Tên Công Ty]**
       - 💡 Độ phù hợp: [Cao/Khá]
       - ✅ Lý do match: [Kỹ năng nào trong CV khớp với Job?]
       - ⚠️ Cần bổ sung: [Job yêu cầu gì mà CV chưa có?]

    🎯 **Top 2: ...**
    
    (Nếu không tìm thấy Job nào phù hợp, hãy đưa ra lời khuyên cải thiện CV dựa trên thị trường).
    """
    
    print("🤖 [CV Review] Đang phân tích độ phù hợp...")
    return get_chat_response(start_chat_session(), prompt), "CV Reviewer Intelligence"
