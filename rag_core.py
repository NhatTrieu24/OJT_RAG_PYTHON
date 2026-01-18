import os
import re
import vertexai
from vertexai.generative_models import GenerativeModel, ChatSession, Tool, Part, FunctionDeclaration
from sqlalchemy import create_engine, text
import agent_adk  # Import file chứa hàm tìm kiếm Vector

# ==================== 1. CẤU HÌNH DATABASE (CODE CỦA BẠN) ====================

# CẤU HÌNH CHO MÁY TÍNH CỦA BẠN (LOCAL)
LOCAL_DB_URL = "postgresql+psycopg2://postgres:NfVTuBOMhVKAVAqxIxZoJCTSLOiqvsgY@trolley.proxy.rlwy.net:14680/railway"

# LOGIC TỰ ĐỘNG CHỌN MÔI TRƯỜNG
if "DATABASE_URL" in os.environ:
    DB_URL = os.environ["DATABASE_URL"]
    # Fix lỗi tương thích cho SQLAlchemy (postgres:// -> postgresql://)
    if DB_URL.startswith("postgres://"):
        DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)
    print("🌍 [CONFIG] Detected Cloud Environment (Railway). Using Internal DB.")
else:
    DB_URL = LOCAL_DB_URL
    print("💻 [CONFIG] Detected Local Environment. Using Public DB.")

# Tạo engine kết nối
try:
    engine = create_engine(DB_URL, pool_size=10, pool_pre_ping=True)
    print(f"🔌 Database Engine created successfully.")
except Exception as e:
    print(f"⚠️ Lỗi cấu hình Database: {e}")

_last_sql = "N/A"

def execute_sql(sql_query):
    """
    Hàm thực thi SQL an toàn, tự động sửa lỗi tên bảng User và log chi tiết.
    """
    global _last_sql
    
    # 1. Dọn dẹp markdown thừa từ AI
    sql_query = sql_query.replace("```sql", "").replace("```", "").strip()

    # 2. Tự động sửa lỗi thiếu ngoặc kép cho bảng User
    sql_query = re.sub(r'(?<!")\bUser\b(?!")', '"User"', sql_query, flags=re.IGNORECASE)
    
    _last_sql = sql_query
    print(f"⚡ [Running SQL]: {sql_query}") 

    try:
        with engine.connect() as conn:
            result_proxy = conn.execute(text(sql_query))
            keys = result_proxy.keys()
            result = result_proxy.mappings().all()
            
            if not result:
                print("⚠️ [SQL Result]: Empty (0 rows)")
                return "Truy vấn thành công nhưng không tìm thấy dữ liệu nào phù hợp."
            
            rows = []
            for row in result:
                row_parts = []
                for k in keys:
                    val = row[k]
                    if val is not None:
                        row_parts.append(f"{k}: {val}")
                row_str = " | ".join(row_parts)
                rows.append(f"- {row_str}")
            
            final_output = "\n".join(rows)
            print(f"✅ [SQL Result]: Found {len(result)} rows.")
            return final_output
            
    except Exception as e:
        error_msg = f"Lỗi thực thi SQL: {str(e)}"
        print(f"❌ [SQL ERROR]: {error_msg}")
        return error_msg

def get_last_sql():
    return _last_sql

def clear_last_sql():
    global _last_sql
    _last_sql = "N/A"

# ==================== 2. CẤU HÌNH VERTEX AI & TOOLS ====================

# Cấu hình Project Google Cloud (Thay bằng Project ID thật của bạn nếu cần)
PROJECT_ID = "reflecting-surf-477600-p4"  
LOCATION = "europe-west4"

try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    print("✅ Vertex AI Initialized.")
except Exception as e:
    print(f"❌ Vertex AI Init Error: {e}")

# --- ĐỊNH NGHĨA TOOLS CHO AI ---

# Tool 1: Tìm kiếm Vector (Semantic Search)
search_vectors_func = FunctionDeclaration(
    name="search_vectors",
    description="Tìm kiếm thông tin trong tài liệu, mô tả công việc, hoặc văn bản dài bằng ngữ nghĩa (Vector Search). Dùng khi câu hỏi mơ hồ, hỏi về mô tả, nội dung, yêu cầu...",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Câu hỏi hoặc từ khóa cần tìm kiếm"
            },
            "target_table": {
                "type": "string",
                "description": "Bảng dữ liệu cần tìm (job_position, document, company, major, ...)"
            }
        },
        "required": ["question"]
    },
)

# Tool 2: Tạo SQL (Structured Query)
generate_sql_func = FunctionDeclaration(
    name="generate_sql_query",
    description="Truy vấn dữ liệu có cấu trúc chính xác (SQL). Dùng khi hỏi về địa chỉ, email, số điện thoại, ngày tháng, số lượng, danh sách cụ thể...",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Câu hỏi gốc của người dùng để chuyển thành SQL"
            }
        },
        "required": ["question"]
    },
)

# Gom nhóm Tools
rag_tools = Tool(
    function_declarations=[search_vectors_func, generate_sql_func],
)

# Khởi tạo Model với Tools
model = GenerativeModel(
    "gemini-2.5-pro", # Hoặc gemini-1.5-flash
    generation_config={"temperature": 0},
    tools=[rag_tools],
)

def start_chat_session():
    """Khởi tạo phiên chat mới"""
    return model.start_chat()

# ==================== 3. HÀM XỬ LÝ CHAT THÔNG MINH ====================

def get_chat_response(chat_session: ChatSession, prompt: str):
    """
    Gửi tin nhắn cho Gemini và tự động xử lý vòng lặp Function Calling.
    """
    # Reset biến debug SQL cho request mới
    clear_last_sql()
    
    try:
        # 1. Gửi câu hỏi đầu tiên
        response = chat_session.send_message(prompt)
        
        # 2. Vòng lặp xử lý: Nếu AI muốn gọi hàm, ta thực thi và gửi lại kết quả
        max_turns = 5
        current_turn = 0

        while current_turn < max_turns:
            try:
                # Kiểm tra an toàn xem có nội dung không
                if not response.candidates or not response.candidates[0].content.parts:
                    break
                part = response.candidates[0].content.parts[0]
            except:
                break 

            # === TRƯỜNG HỢP 1: AI MUỐN GỌI HÀM (Function Call) ===
            if part.function_call:
                func_name = part.function_call.name
                func_args = dict(part.function_call.args)
                
                print(f"🔄 [AI Action] Calling function: {func_name} | Args: {func_args}")
                
                api_response = {}
                
                # Xử lý: search_vectors
                if func_name == "search_vectors":
                    # Gọi hàm từ agent_adk.py
                    result = agent_adk.search_vectors(
                        question=func_args.get("question"),
                        target_table=func_args.get("target_table", "document")
                    )
                    api_response = {"result": result}
                    
                # Xử lý: generate_sql_query
                elif func_name == "generate_sql_query":
                    # Bước 1: Hỏi AI để lấy câu SQL (Prompt phụ)
                    sql_gen_model = GenerativeModel("gemini-2.5-pro")
                    # Schema rút gọn để AI hiểu cấu trúc DB
                    db_schema = """
                    Tables:
                    - Company(company_id, name, address, website, email, phone, tax_code)
                    - Job_Position(job_position_id, job_title, requirements, salary, location, company_id)
                    - "User"(user_id, fullname, email, phone, address, role)
                    - Semester(semester_id, semester_name, start_date, end_date)
                    - Major(major_id, major_title, major_code)
                    """
                    sql_prompt = f"Bạn là chuyên gia SQL PostgreSQL. Dựa vào schema sau:\n{db_schema}\n\nHãy viết câu lệnh SQL để trả lời: '{func_args.get('question')}'. Chỉ trả về code SQL, không giải thích."
                    
                    try:
                        sql_resp = sql_gen_model.generate_content(sql_prompt)
                        generated_sql = sql_resp.text
                        
                        # Bước 2: Chạy SQL bằng hàm execute_sql ở trên
                        sql_result = execute_sql(generated_sql)
                        api_response = {"result": sql_result}
                    except Exception as sqle:
                        api_response = {"error": str(sqle)}
                
                else:
                    api_response = {"error": "Unknown function"}

                # Gửi kết quả chạy hàm NGƯỢC LẠI cho AI
                response = chat_session.send_message(
                    Part.from_function_response(
                        name=func_name,
                        response=api_response
                    )
                )
                current_turn += 1
                continue # Quay lại đầu vòng lặp

            # === TRƯỜNG HỢP 2: AI TRẢ LỜI TEXT (Đã có kết quả) ===
            else:
                return response.text

        return "Xin lỗi, hệ thống đang bận, vui lòng thử lại sau."

    except Exception as e:
        print(f"❌ Lỗi xử lý chat: {e}")
        return "Đã xảy ra lỗi trong quá trình xử lý yêu cầu."