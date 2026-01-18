import os
import time
import psycopg2
import vertexai
from vertexai.language_models import TextEmbeddingModel
from google.oauth2 import service_account
from google.api_core.exceptions import ResourceExhausted

# ==================== CẤU HÌNH ====================
# 1. Cấu hình Database (Docker)
DB_CONFIG = "postgresql://postgres:123@caboose.proxy.rlwy.net:54173/railway"
# Thay các thông tin bằng cái bạn vừa lấy trên Railway

# Tìm xuống dưới và sửa dòng connect:
# conn = psycopg2.connect(dsn=RAILWAY_URL)
# 2. Cấu hình Google Vertex AI
KEY_PATH = "rag-service-account.json" 
PROJECT_ID = "reflecting-surf-477600-p4"
LOCATION = "europe-west4"

# ==================== KHỞI TẠO ====================
print("🚀 Đang khởi tạo Vertex AI...")
try:
    if os.path.exists(KEY_PATH):
        credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
        vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)
    else:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        
    model = TextEmbeddingModel.from_pretrained("text-embedding-004")
    print("✅ Đã kết nối Google Vertex AI thành công!")
except Exception as e:
    print(f"❌ Lỗi kết nối Google AI: {e}")
    exit()

# ==================== HÀM XỬ LÝ (QUAN TRỌNG) ====================
def get_embedding(text):
    """
    Lấy vector với cơ chế 'Phanh' và 'Thử lại' thông minh
    """
    if not text or len(str(text).strip()) < 2: return None
    
    # Cắt ngắn text để tránh lỗi quá dài (Google giới hạn token)
    safe_text = str(text)[:8000]
    
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # Gọi Google AI
            embeddings = model.get_embeddings([safe_text])
            
            # --- QUAN TRỌNG: CHỦ ĐỘNG NGỦ 1 GIÂY SAU MỖI LẦN GỌI ---
            # Giúp giảm tốc độ xuống < 60 request/phút để không bị chặn
            time.sleep(1) 
            
            return embeddings[0].values

        except ResourceExhausted:
            # Nếu bị lỗi 429 (Quota exceeded)
            wait_time = 30 * (attempt + 1) # Đợi 30s, 60s, 90s...
            print(f"\n   😴 Google báo quá tải (429). Đang nghỉ {wait_time}s để hồi phục...")
            time.sleep(wait_time)
            
        except Exception as e:
            if "429" in str(e): # Bắt lỗi 429 dạng string
                wait_time = 30 * (attempt + 1)
                print(f"\n   😴 Google báo quá tải (429). Đang nghỉ {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"\n   ⚠️ Lỗi khác: {e}")
                return None
    
    print("\n   ❌ Đã thử 5 lần nhưng vẫn thất bại. Bỏ qua dòng này.")
    return None

def process_table(conn, table_name, id_col, text_cols):
    cur = conn.cursor()
    tbl_sql = f'"{table_name}"' if table_name == "User" else table_name
    
    print(f"\n📂 Đang xử lý bảng: {table_name}...")
    
    # Chỉ lấy dòng chưa có vector
    cols_select = ", ".join(text_cols)
    sql = f"SELECT {id_col}, {cols_select} FROM {tbl_sql} WHERE embedding IS NULL"
    cur.execute(sql)
    rows = cur.fetchall()
    
    if not rows:
        print("   -> ✅ Dữ liệu đã đầy đủ.")
        return

    print(f"   -> 📦 Tìm thấy {len(rows)} dòng cần xử lý.")
    
    count = 0
    for row in rows:
        row_id = row[0]
        
        # Ghép text
        parts = []
        for idx, val in enumerate(row[1:]):
            if val: parts.append(f"{val}")
        full_text = ". ".join(parts)
        
        # Lấy vector
        vector = get_embedding(full_text)
        
        if vector:
            sql_update = f"UPDATE {tbl_sql} SET embedding = %s WHERE {id_col} = %s"
            cur.execute(sql_update, (vector, row_id))
            conn.commit() # Lưu ngay lập tức từng dòng
            count += 1
            print(".", end="", flush=True) # In dấu chấm tiến trình
                
    print(f"\n   -> 🎉 Đã xong bảng {table_name}.")

# ==================== MAIN ====================
if __name__ == "__main__":
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        print("🔌 Đã kết nối Database Docker.")

        tasks = [
            ("job_position", "job_position_id", ["job_title", "requirements", "location"]),
            ("company", "company_id", ["name", "address", "website"]),
            ("major", "major_id", ["major_title", "description"]),
            ("companydocument", "companydocument_id", ["title"]),
            ("ojtdocument", "ojtdocument_id", ["title"]),
            ("User", "user_id", ["fullname", "email"]) 
        ]

        for task in tasks:
            process_table(conn, task[0], task[1], task[2])

        print("\n" + "="*40)
        print("✅✅✅ HOÀN TẤT! DATABASE ĐÃ SẴN SÀNG.")
        print("="*40)

    except Exception as e:
        print(f"\n❌ Lỗi: {e}")
    finally:
        if 'conn' in locals() and conn: conn.close()