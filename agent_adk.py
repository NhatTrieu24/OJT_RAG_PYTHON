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

# 1.2 Cấu hình AI Local (LAZY LOADING - QUAN TRỌNG CHO RENDER)
# Không tải model ngay lập tức để tránh Timeout khi khởi động
local_embedder = None

def get_embedder():
    """Hàm tải model 'lười' - Chỉ tải khi cần dùng"""
    global local_embedder
    if local_embedder is None:
        print("⏳ [AI Local] Đang tải Model Embedding (MiniLM)...")
        from sentence_transformers import SentenceTransformer
        local_embedder = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
        print("✅ [AI Local] Model đã sẵn sàng!")
    return local_embedder

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

# ==================== 3. HÀM VECTOR LOCAL (ĐÃ SỬA LAZY LOAD) ====================

def get_embeddings_batch(texts):
    """Tạo Vector bằng CPU Server (Free & Fast)"""
    embedder = get_embedder() # <--- Gọi hàm lazy load
    if not embedder or not texts: return []
    
    # Cắt ngắn text để tránh lỗi model limit
    clean_texts = [str(t).replace("\n", " ").strip()[:1000] for t in texts if t]
    try:
        embeddings = embedder.encode(clean_texts)
        return embeddings.tolist()
    except Exception as e:
        print(f"⚠️ Local Embed Error: {e}")
        return []

def get_query_embedding(text):
    """Tạo vector cho 1 câu hỏi"""
    embedder = get_embedder() # <--- Gọi hàm lazy load
    try:
        embedding = embedder.encode(text)
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
                    # Lấy kết quả có khoảng cách < 0.85 (Đã nới lỏng để lấy nhiều dữ liệu hơn)
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
    
    # 2. Tìm kiếm Vector
    db_context = search_vectors(clean_question) 
    
    # 3. KÍCH HOẠT ĐỌC FILE DRIVE TRỰC TIẾP (QUAN TRỌNG)
    realtime_file_content = ""
    source_link = ""
    
    if "drive.google.com" in db_context:
        link_match = re.search(r'https://drive\.google\.com/[^\s]+', db_context)
        if link_match:
            target_url = link_match.group(0).rstrip(").,")
            print(f"🚀 [Real-time] Đang đọc chi tiết: {target_url}")
            
            realtime_file_content = get_text_from_drive(target_url)
            
            if realtime_file_content:
                source_link = target_url
                print(f"   ✅ Đã trích xuất được {len(realtime_file_content)} ký tự chi tiết.")

    # 4. Tạo Prompt
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
       - Vị trí tuyển dụng & Yêu cầu kỹ năng.
       - Quyền lợi (Lương, trợ cấp, môi trường).
       - Cách thức ứng tuyển (Email, Quy trình).
    2. Trình bày rõ ràng, gạch đầu dòng.
    3. Nếu có link tài liệu gốc ({source_link}), HÃY ĐỂ NÓ Ở CUỐI CÙNG.
    """
    
    try:
        chat_session = start_chat_session()
        answer = get_chat_response(chat_session, final_prompt)
    except Exception as e:
        answer = "⚠️ Hệ thống đang bận. Vui lòng thử lại sau."
        print(f"❌ Chat Error: {e}")
    
    print(f"⏱️ Total Time: {time.time() - t_start:.3f}s")
    mode_label = "RAG + Realtime" if realtime_file_content else "RAG Fast"
    return answer, mode_label

# ==================== 6. ĐỒNG BỘ DỮ LIỆU (SYNC ALL) ====================
SYNC_STATE = {
    "is_running": False,
    "step": "Sẵn sàng",
    "detail": "",
    "processed": 0,
    "total_estimate": 0
}
def sync_all_data(force_reset=False):
    global SYNC_STATE
    
    print(f"🔄 [Sync] Bắt đầu đồng bộ dữ liệu...")
    # Cập nhật trạng thái bắt đầu
    SYNC_STATE["is_running"] = True
    SYNC_STATE["step"] = "Đang khởi động..."
    SYNC_STATE["processed"] = 0
    
    t_start = time.time()
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if force_reset:
                    SYNC_STATE["step"] = "Đang xóa dữ liệu cũ (Reset)..."
                    print("⚠️ [Reset] Đang xóa vector cũ...")
                    tables = ["job_position", "company", "semester", "User", "major", "ojtdocument", "companydocument", "finalreport", "job_title_overview"]
                    for t in tables:
                        cur.execute(f"SELECT to_regclass('public.\"{t}\"');")
                        if cur.fetchone()[0]:
                            cur.execute(f'UPDATE "{t}" SET embedding = NULL, last_content_indexed = NULL;')
                    conn.commit()

                # --- ĐỊNH NGHĨA KỊCH BẢN ---
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


                # --- LOOP XỬ LÝ (SỬA ĐOẠN NÀY ĐỂ BÁO CÁO TIẾN ĐỘ) ---
                for sc in scenarios:
                    table = sc['table']
                    id_col = sc['id']
                    
                    # Update trạng thái: Đang quét bảng nào
                    SYNC_STATE["step"] = f"Đang quét bảng: {table}"
                    
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
                        
                        # --- CẬP NHẬT TIẾN ĐỘ CHI TIẾT ---
                        SYNC_STATE["step"] = f"Đang xử lý {table}"
                        SYNC_STATE["detail"] = f"Batch {i//BATCH_SIZE + 1} ({len(batch)} dòng)"
                        SYNC_STATE["processed"] += len(batch)

                        for r in batch:
                            rid = r[0]
                            # Xử lý File Drive
                            if table in ["ojtdocument", "companydocument"]:
                                title = r[1]
                                url = r[2] if len(r) > 2 else ""
                                content = ""
                                if "drive.google.com" in url:
                                    # Báo cáo đang đọc file nào
                                    SYNC_STATE["detail"] = f"Đang đọc file: {title[:15]}..."
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
                            print(f"   ✅ Saved batch {i//BATCH_SIZE + 1}.")

        print(f"🎉 [Sync] Hoàn tất sau {time.time() - t_start:.2f}s.")
        SYNC_STATE["step"] = "Hoàn tất"
        SYNC_STATE["detail"] = f"Tổng thời gian: {time.time() - t_start:.2f}s"
        
    except Exception as e:
        print(f"❌ Lỗi Sync: {e}")
        SYNC_STATE["step"] = "Lỗi"
        SYNC_STATE["detail"] = str(e)
    finally:
        # Đợi 5s rồi tắt trạng thái running để FE kịp đọc thông báo "Hoàn tất"
        time.sleep(5) 
        SYNC_STATE["is_running"] = False
# ==================== 7. CV REVIEW (MATCH MAKER) ====================

# ==================== 7. CV REVIEW (PHIÊN BẢN CHUYÊN GIA CAO CẤP) ====================

def run_cv_review(cv_text: str, user_message: str):
    from rag_core import start_chat_session, get_chat_response
    
    # 1. Kiểm tra đầu vào
    print(f"📄 [CV Review] Đang đọc CV: {len(cv_text)} ký tự.")
    if len(cv_text) < 100:
        return "⚠️ Lỗi: Không đọc được nội dung CV (File ảnh hoặc lỗi font).", "CV Error"

    # 2. Tìm kiếm Job phù hợp trong DB
    # Thêm từ khóa "JD" và "Mô tả công việc" để tìm đúng file tuyển dụng
    search_query = cv_text[:500] + " tuyển dụng JD Job Description yêu cầu kỹ năng lập trình"
    db_context = search_vectors(search_query)
    
    # 3. --- TÍNH NĂNG MỚI: ĐỌC CHI TIẾT FILE JD (Real-time) ---
    # Nếu Vector Search tìm thấy link Drive của JD, ta sẽ tải về đọc ngay lập tức
    detailed_jds = ""
    found_links = re.findall(r'https://drive\.google\.com/[^\s]+', db_context)
    
    # Chỉ đọc tối đa 2 file JD liên quan nhất để không bị quá tải
    if found_links:
        print(f"🚀 [CV Match] Phát hiện {len(found_links)} JD tiềm năng. Đang đọc chi tiết...")
        unique_links = list(set(found_links))[:2] # Lấy 2 link đầu tiên (thường là match nhất)
        
        for idx, link in enumerate(unique_links):
            link = link.rstrip(").,")
            content = get_text_from_drive(link) # Hàm này đã có sẵn trong agent_adk.py
            if content:
                detailed_jds += f"\n--- CHI TIẾT JD SỐ {idx+1} ({link}) ---\n{content[:4000]}\n" # Cắt bớt nếu quá dài
                print(f"   ✅ Đã đọc xong JD số {idx+1}")
    else:
        detailed_jds = "Không đọc được file chi tiết (Chỉ dùng tóm tắt hệ thống)."

    # 4. Prompt Chuyên Gia Tuyển Dụng (Siêu chi tiết)
    prompt = f"""
    VAI TRÒ: Bạn là Chuyên gia Tuyển dụng (Senior Tech Recruiter) với 20 năm kinh nghiệm.
    
    DỮ LIỆU ĐẦU VÀO:
    --------------------------------------------------
    1. HỒ SƠ ỨNG VIÊN (CV):
    {cv_text[:3000]}
    
    2. DANH SÁCH VIỆC LÀM TÌM THẤY (Tóm tắt):
    {db_context}
    
    3. NỘI DUNG JD ĐẦY ĐỦ (QUAN TRỌNG - Ưu tiên dùng thông tin này):
    {detailed_jds}
    
    4. YÊU CẦU CỦA NGƯỜI DÙNG: "{user_message}"
    --------------------------------------------------
    
    NHIỆM VỤ:
    Hãy đóng vai người Mentor, phân tích kỹ lưỡng sự phù hợp giữa CV và các vị trí tìm được.
    Tuyệt đối KHÔNG trả lời chung chung. Phải đưa ra dẫn chứng cụ thể từ CV và JD.
    
    ĐỊNH DẠNG CÂU TRẢ LỜI (Bắt buộc tuân thủ):
    
    🌟 **ĐỀ XUẤT SỐ 1: [Tên Vị Trí] - [Tên Công Ty]**
       * **🎯 Độ phù hợp:** [Điểm số/10] (Dựa trên kỹ năng khớp)
       * **✅ Tại sao bạn phù hợp (Matching Points):**
           - CV bạn có kỹ năng [A] khớp với yêu cầu [B] trong JD.
           - Bạn đã làm đồ án [C] liên quan đến mảng [D] của công ty.
       * **⚠️ Điểm còn thiếu (Gap Analysis):**
           - Công ty yêu cầu [X] (có trong JD) nhưng CV bạn chưa thấy nhắc đến.
           - Cần cải thiện thêm về [Kỹ năng mềm/Tiếng Anh] theo yêu cầu của họ.
       * **🎁 Quyền lợi nổi bật (Nếu có trong JD):** [Lương/Trợ cấp/Môi trường...]
       * **🔗 Link tài liệu:** [Link file Drive hoặc Email nếu có]

    🌟 **ĐỀ XUẤT SỐ 2: ...** (Tương tự)

    💡 **LỜI KHUYÊN TỔNG QUÁT:**
    (Đưa ra 1 lời khuyên đắt giá để ứng viên cải thiện CV này tốt hơn).
    """
    
    print("🤖 [CV Review] Đang phân tích sâu...")
    # Gọi AI trả lời
    answer = get_chat_response(start_chat_session(), prompt)
    
    return answer, "CV Matcher"
