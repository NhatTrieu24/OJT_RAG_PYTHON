import re
from sqlalchemy import create_engine, text

# ==================== CẤU HÌNH DATABASE ====================
# User: ai_read_only
# Pass: AI@123  --> Mã hóa URL thành: AI%40123 (Vì @ là ký tự đặc biệt)
# DB:   OJT_RAG

# DB_URL = "postgresql://ai_read_only:AI%40123@localhost:5432/OJT_RAG"
DB_URL = "postgresql+psycopg2://postgres:123456@localhost:5432/OJT_RAG"
# Tạo engine kết nối
try:
    # pool_pre_ping=True giúp tự động kết nối lại nếu bị ngắt
    engine = create_engine(DB_URL, pool_size=10, pool_pre_ping=True)
    print("🔌 Database Engine created successfully (User: ai_read_only).")
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
    # Logic: Tìm chữ User mà xung quanh KHÔNG có dấu ngoặc kép -> Thêm vào -> "User"
    sql_query = re.sub(r'(?<!")\bUser\b(?!")', '"User"', sql_query, flags=re.IGNORECASE)
    
    _last_sql = sql_query

    print(f"⚡ [Running SQL]: {sql_query}") 

    try:
        with engine.connect() as conn:
            # Chạy SQL
            result_proxy = conn.execute(text(sql_query))
            
            # Lấy tên cột (keys) để mapping
            keys = result_proxy.keys()
            
            # Lấy dữ liệu
            result = result_proxy.mappings().all()
            
            if not result:
                print("⚠️ [SQL Result]: Empty (0 rows)")
                return "Truy vấn thành công nhưng không tìm thấy dữ liệu nào phù hợp."
            
            # Format kết quả
            rows = []
            for row in result:
                row_parts = []
                for k in keys:
                    val = row[k]
                    # Convert các kiểu dữ liệu đặc biệt (Date, Boolean) thành chuỗi
                    if val is not None:
                        row_parts.append(f"{k}: {val}")
                
                row_str = " | ".join(row_parts)
                rows.append(f"- {row_str}")
            
            final_output = "\n".join(rows)
            
            # Log kết quả rút gọn
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