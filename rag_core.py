import os
import re
import vertexai
from vertexai.generative_models import GenerativeModel, ChatSession, Tool, Part, FunctionDeclaration
from sqlalchemy import create_engine, text
from tenacity import retry, stop_after_attempt, wait_fixed
import agent_adk  # Import file tìm kiếm Vector đã tối ưu trước đó

# ==================== 1. CẤU HÌNH DATABASE (Lazy Loading) ====================

# Cấu hình URL
LOCAL_DB_URL = "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway"
DB_URL = os.environ.get("DATABASE_URL", LOCAL_DB_URL)

# Fix lỗi tương thích SQLAlchemy trên Cloud
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

_db_engine = None

def get_engine():
    """Tạo Engine kết nối DB theo cơ chế Singleton (Chỉ tạo 1 lần)"""
    global _db_engine
    if _db_engine is None:
        try:
            _db_engine = create_engine(
                DB_URL, 
                pool_size=10, 
                pool_recycle=3600, 
                pool_pre_ping=True # Tự động kết nối lại nếu bị ngắt
            )
            print("🔌 [DB] Database Engine initialized.")
        except Exception as e:
            print(f"⚠️ [DB] Connection Error: {e}")
    return _db_engine

def execute_sql(sql_query):
    """Thực thi SQL an toàn, tự động fix lỗi tên bảng User"""
    engine = get_engine()
    if not engine: return "Lỗi kết nối Database."

    # 1. Dọn dẹp markdown
    sql_query = re.sub(r"```sql|```", "", sql_query, flags=re.IGNORECASE).strip()
    
    # 2. Fix lỗi bảng "User" (Postgres case-sensitive)
    sql_query = re.sub(r'(?<!")\bUser\b(?!")', '"User"', sql_query, flags=re.IGNORECASE)
    
    print(f"⚡ [SQL Exec]: {sql_query}")

    try:
        with engine.connect() as conn:
            # Giới hạn chỉ đọc để an toàn (Optional)
            if not sql_query.lower().startswith("select"):
                return "Chỉ cho phép câu lệnh SELECT để tra cứu dữ liệu."

            result_proxy = conn.execute(text(sql_query))
            keys = result_proxy.keys()
            result = result_proxy.mappings().all()
            
            if not result:
                return "Không tìm thấy dữ liệu nào phù hợp trong Database."
            
            # Format kết quả dạng text gọn gàng cho AI đọc
            rows = []
            for row in result[:10]: # Chỉ lấy 10 dòng đầu để tránh tràn context
                row_str = " | ".join([f"{k}: {row[k]}" for k in keys if row[k] is not None])
                rows.append(f"- {row_str}")
            
            return "\n".join(rows)
            
    except Exception as e:
        error_msg = str(e)
        print(f"❌ [SQL Error]: {error_msg}")
        return f"Lỗi cú pháp SQL: {error_msg}"

# ==================== 2. CẤU HÌNH VERTEX AI & TOOLS ====================

PROJECT_ID = os.getenv("PROJECT_ID", "reflecting-surf-477600-p4")
LOCATION = os.getenv("LOCATION", "us-central1") # Khuyên dùng us-central1 cho ổn định

try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    print(f"✅ [Vertex AI] Connected: {PROJECT_ID}")
except Exception as e:
    print(f"❌ [Vertex AI] Init Error: {e}")

# --- ĐỊNH NGHĨA TOOLS ---

search_vectors_func = FunctionDeclaration(
    name="search_vectors",
    description="Tìm kiếm ngữ nghĩa (Semantic Search) trong tài liệu OJT, mô tả công việc, nội dung file PDF/Word. Dùng cho các câu hỏi: 'Quy định về...', 'Mô tả công việc...', 'Tìm tài liệu...'",
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Câu hỏi cần tìm kiếm"},
            "limit": {"type": "integer", "description": "Số lượng kết quả (mặc định 5)"}
        },
        "required": ["question"]
    },
)

generate_sql_func = FunctionDeclaration(
    name="generate_sql_query",
    description="Tra cứu dữ liệu chính xác bằng SQL. Dùng cho câu hỏi về: Số liệu, Danh sách sinh viên, Email, Số điện thoại, Lương cụ thể, Ngày tháng.",
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Câu hỏi gốc cần chuyển thành SQL"}
        },
        "required": ["question"]
    },
)

rag_tools = Tool(function_declarations=[search_vectors_func, generate_sql_func])

SYSTEM_INSTRUCTION = """
VAI TRÒ: OJT AI ASSISTANT (Thông minh - Trung thực - Dựa trên dữ liệu).

1. ƯU TIÊN SỬ DỤNG TOOL:
   - Nếu câu hỏi cần tra cứu quy định, tài liệu, mô tả: Gọi 'search_vectors'.
   - Nếu câu hỏi cần danh sách, số liệu, thông tin cụ thể (Email, SĐT): Gọi 'generate_sql_query'.
   
2. NGUYÊN TẮC TRẢ LỜI:
   - Chỉ trả lời dựa trên kết quả trả về từ Tool.
   - Nếu có Link tài liệu (file_url), BẮT BUỘC phải đính kèm vào cuối câu trả lời.
   - Nếu Tool trả về rỗng, hãy nói: "Hiện tại hệ thống chưa có thông tin về vấn đề này."
   
3. KHÔNG BỊA ĐẶT: Tuyệt đối không tự sáng tác quy định hoặc thông tin liên hệ.
"""

# Model chính để Chat (Có khả năng gọi Tool)
# Lưu ý: Vertex AI hỗ trợ tốt nhất function calling trên gemini-1.5-pro hoặc gemini-1.5-flash
chat_model = GenerativeModel(
    model_name="gemini-2.0-flash-001", # Flash nhanh và rẻ hơn, Pro thông minh hơn
    generation_config={"temperature": 0.0}, # 0.0 để AI chọn Tool chính xác nhất
    system_instruction=SYSTEM_INSTRUCTION,
    tools=[rag_tools]
)

# Model phụ chuyên viết SQL (Tách riêng để tối ưu Prompt)
sql_gen_model = GenerativeModel(
    model_name="gemini-2.0-flash-exp",
    generation_config={"temperature": 0.0} # Bắt buộc 0.0 để SQL chuẩn xác
)

DB_SCHEMA = """
SCHEMA:
- Company(company_id, name, address, website, contact_email)
- Job_Position(job_title, requirements, salary_range, location, company_id)
- "User"(fullname, email, student_code, role, major_id)
- Major(major_title, major_code)
- Semester(name, start_date, end_date)
"""

def generate_sql_helper(question):
    """Hàm phụ trợ để sinh SQL từ câu hỏi"""
    prompt = f"""
    {DB_SCHEMA}
    Yêu cầu: Viết câu lệnh PostgreSQL để trả lời: "{question}".
    Quy tắc: 
    1. Chỉ dùng SELECT. 
    2. ILIKE cho tìm kiếm văn bản. 
    3. Trả về duy nhất code SQL, không markdown.
    4. Bảng User phải để trong ngoặc kép: "User".
    """
    try:
        response = sql_gen_model.generate_content(prompt)
        return response.text.strip()
    except:
        return ""

# ==================== 3. LOGIC CHAT THÔNG MINH (LOOP) ====================

def start_chat_session():
    return chat_model.start_chat()

def get_chat_response(chat_session: ChatSession, prompt: str):
    """Xử lý vòng lặp gọi Tool tự động"""
    
    # Gửi tin nhắn đầu tiên
    try:
        response = chat_session.send_message(prompt)
    except Exception as e:
        return f"⚠️ Lỗi kết nối AI: {e}"

    # Vòng lặp xử lý (Tối đa 5 lần gọi tool liên tiếp)
    current_turn = 0
    while current_turn < 5:
        try:
            # Kiểm tra xem AI có muốn gọi hàm không
            if not response.candidates or not response.candidates[0].content.parts:
                break
            
            part = response.candidates[0].content.parts[0]
            
            # Nếu là Text thường -> Trả về luôn
            if not part.function_call:
                return response.text
            
            # === AI MUỐN GỌI HÀM ===
            func_name = part.function_call.name
            args = dict(part.function_call.args)
            print(f"🔧 [Tool Call] {func_name} | Args: {args}")
            
            api_result = {}
            
            # 1. Xử lý Vector Search
            if func_name == "search_vectors":
                q = args.get("question")
                # Gọi hàm search_vectors đã tối ưu bên agent_adk
                raw_res = agent_adk.search_vectors(q, limit=5)
                api_result = {"result": raw_res}

            # 2. Xử lý SQL Query
            elif func_name == "generate_sql_query":
                q = args.get("question")
                generated_sql = generate_sql_helper(q) # Gọi AI viết SQL
                if generated_sql:
                    sql_res = execute_sql(generated_sql) # Chạy SQL
                    api_result = {"sql": generated_sql, "data": sql_res}
                else:
                    api_result = {"error": "Không thể tạo câu lệnh SQL."}

            else:
                api_result = {"error": "Hàm không tồn tại."}

            # Gửi kết quả Tool trở lại cho AI
            response = chat_session.send_message(
                Part.from_function_response(
                    name=func_name,
                    response=api_result
                )
            )
            current_turn += 1
            
        except Exception as e:
            print(f"❌ Error in chat loop: {e}")
            return "Xin lỗi, đã xảy ra lỗi trong quá trình xử lý."

    return response.text