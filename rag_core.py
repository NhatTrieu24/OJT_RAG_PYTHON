import os
import re
from sqlalchemy import create_engine, text

# ==================== CẤU HÌNH DATABASE THÔNG MINH ====================

# 1. CẤU HÌNH CHO MÁY TÍNH CỦA BẠN (LOCAL)
# Lưu ý: 'postgres.railway.internal' CHỈ chạy được trên server Railway.
# Ở máy nhà, bạn phải dùng Host Public (thường là roundhouse.proxy.rlwy.net...).
# Bạn hãy thay 'HOST_PUBLIC' và 'PORT_PUBLIC' bằng thông tin trong tab Variables.
LOCAL_DB_URL = "postgresql+psycopg2://postgres:NfVTuBOMhVKAVAqxIxZoJCTSLOiqvsgY@trolley.proxy.rlwy.net:14680/railway"
# 2. LOGIC TỰ ĐỘNG CHỌN MÔI TRƯỜNG
# - Nếu có biến DATABASE_URL (khi deploy lên Railway) -> Dùng nó (Internal).
# - Nếu không (chạy máy nhà) -> Dùng LOCAL_DB_URL (Public).

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