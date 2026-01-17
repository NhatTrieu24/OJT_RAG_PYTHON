import os
import re
import vertexai
from vertexai.generative_models import (
    GenerativeModel, Tool, FunctionDeclaration, GenerationConfig, Part
)
from rag_core import execute_sql, get_last_sql, clear_last_sql

# ==================== 1. CẤU HÌNH HỆ THỐNG ====================
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4" 

# --- TỰ ĐỘNG NẠP CREDENTIALS ---
key_path = os.path.join(os.getcwd(), "rag-service-account.json")
if os.path.exists(key_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path
    print(f"🔑 [ADK] Đã nạp Credentials từ: {key_path}")
else:
    print("⚠️ [ADK] Cảnh báo: Không tìm thấy file rag-service-account.json!")

try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
except Exception as e:
    print(f"⚠️ Vertex AI Init Error: {e}")

# ==================== 2. ĐỊNH NGHĨA CÔNG CỤ ====================
sql_tool = Tool(
    function_declarations=[
        FunctionDeclaration(
            name="query_ojt_database",
            description="Chạy câu lệnh SQL PostgreSQL để truy xuất dữ liệu.",
            parameters={
                "type": "object",
                "properties": {
                    "sql_query": {
                        "type": "string", 
                        "description": "Câu lệnh SQL chuẩn. Phải tuân thủ các quy tắc Business Logic (is_active, mapping)."
                    }
                },
                "required": ["sql_query"]
            }
        )
    ]
)

# ==================== 3. BỘ NÃO THÔNG MINH (SYSTEM PROMPT V6.0 - FINAL) ====================
SYSTEM_INSTRUCTION = """
BẠN LÀ TRỢ LÝ ẢO THÔNG MINH HỖ TRỢ SINH VIÊN OJT.

NHIỆM VỤ:
1. Trả lời câu hỏi dựa trên Database (SQL) hoặc Tài liệu.
2. Cross-Language Search (Dịch từ khóa Việt -> Anh).

--- QUY TẮC SQL & BUSINESS LOGIC (TUÂN THỦ TUYỆT ĐỐI) ---

RULE 1: TÊN BẢNG & QUYỀN TRUY CẬP
- Bảng người dùng là `"User"` (có dấu ngoặc kép, chữ U hoa).
- Khi truy vấn bảng này: `SELECT ... FROM "User" ...`

RULE 2: TÌM VIỆC LÀM (JOB SEARCH)
- Mặc định phải tìm job đang mở: `jp.is_active = true`.
- Về trạng thái công ty (`semester_company`): Vì dữ liệu có thể chưa cập nhật, hãy chấp nhận cả NULL.
  -> `(sc.status = 'active' OR sc.status IS NULL)`

RULE 3: MAPPING ĐỊA ĐIỂM (GEO MAPPING)
- DB lưu không dấu ("Hanoi", "Ho Chi Minh"). User hỏi có dấu ("Hà Nội").
- "Hà Nội" -> `(location ILIKE '%Hanoi%' OR location ILIKE '%Ha Noi%' OR location ILIKE '%Hà Nội%')`
- "HCM"/"Sài Gòn" -> `(location ILIKE '%Ho Chi Minh%' OR location ILIKE '%HCM%')`

RULE 4: MAPPING TỪ KHÓA (KEYWORD MAPPING)
- "Lập trình viên" -> `(job_title ILIKE '%Developer%' OR job_title ILIKE '%Engineer%' OR job_title ILIKE '%Programmer%')`
- "An ninh mạng"/"Bảo mật" -> `(job_title ILIKE '%Security%' OR job_title ILIKE '%Cyber%')`
- "Thực tập sinh" -> `(job_title ILIKE '%Intern%')`

RULE 5: KIỂM TRA TRẠNG THÁI (CÒN TUYỂN KHÔNG?)
- Nếu user hỏi "Còn tuyển không?", ĐỪNG lọc `is_active = true`.
- Hãy SELECT cột `is_active` để trả lời.
"""

# Khởi tạo Model
model = GenerativeModel(
    "gemini-2.5-pro", 
    tools=[sql_tool],
    system_instruction=SYSTEM_INSTRUCTION
)

# ==================== 4. HÀM XỬ LÝ TEXT AN TOÀN ====================
def get_safe_response_text(response):
    """Đảm bảo không crash khi model trả về FunctionCall không có text."""
    try:
        if hasattr(response, 'text') and response.text:
            return response.text
    except Exception:
        pass 

    try:
        final_text = []
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.text:
                    final_text.append(part.text)
        
        result = "\n".join(final_text).strip()
        if result:
            return result
    except Exception:
        pass

    return "" 

# ==================== 5. LOGIC CHÍNH ====================
def run_agent(user_message, file_content=None):
    clear_last_sql()
    chat = model.start_chat()
    
    try:
        with open("rag_brain.txt", "r", encoding="utf-8") as f:
            brain = f.read()
    except:
        brain = "Bạn là trợ lý ảo OJT."

    # Tiền xử lý Input
    clean_msg = re.sub(r'\b25\b', '2025', user_message)
    
    prompt_suffix = "\n[LƯU Ý]: Kiểm tra kỹ mapping địa điểm (Hanoi) và từ khóa (Developer, Security)."

    if file_content:
        full_prompt = f"{brain}\n\n=== DOCUMENT ===\n{file_content}\n\nUSER REQUEST: {clean_msg}{prompt_suffix}"
    else:
        full_prompt = f"{brain}\n\nUSER REQUEST: {clean_msg}{prompt_suffix}"

    try:
        # Gửi Prompt
        response = chat.send_message(
            full_prompt, 
            generation_config=GenerationConfig(temperature=0.0)
        )
        
        # XỬ LÝ FUNCTION CALL
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    args = part.function_call.args
                    sql = args.get("sql_query") or args.get("user_query")
                    
                    print(f"🤖 AI Thinking & SQL: {sql}")
                    
                    # Thực thi SQL
                    db_result = execute_sql(sql)
                    
                    if not db_result:
                        db_result = "QUERY RETURNED NO DATA. (Check SQL logic or Keywords)"

                    # Gửi kết quả DB lại cho AI
                    final_res = chat.send_message(
                        [Part.from_function_response(name="query_ojt_database", response={"content": str(db_result)})]
                    )
                    return get_safe_response_text(final_res), get_last_sql()

        safe_text = get_safe_response_text(response)
        if not safe_text:
            return "Xin lỗi, tôi đang xử lý dữ liệu nhưng gặp trục trặc khi tạo câu trả lời.", get_last_sql()
            
        return safe_text, get_last_sql()

    except Exception as e:
        print(f"❌ Error in Agent: {e}")
        return f"Hệ thống đang bận, vui lòng thử lại sau. (Chi tiết: {str(e)})", get_last_sql()