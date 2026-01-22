import os
import time
import vertexai
from vertexai.generative_models import GenerativeModel
from google.oauth2 import service_account
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, NotFound, PermissionDenied

# ==================== CẤU HÌNH PROJECT ====================
PROJECT_ID = "reflecting-surf-477600-p4"  # Project ID của bạn
LOCATION = "us-central1"                  # Server ổn định nhất của Google
CREDENTIALS_FILE = "credentials.json"     # File key JSON tải từ Google Cloud

# Danh sách tên Model chuẩn trên Vertex AI (Khác với AI Studio nhé!)
# Vertex AI không dùng tiền tố "models/"
VERTEX_MODELS_TO_TEST = [
    "gemini-2.0-flash-exp",    # Bản Flash ổn định (Nên dùng)
    "gemini-2.0-flash-001",    # Bản Flash cập nhật mới hơn
    "gemini-2.5-pro",      # Bản Pro ổn định    # Bản cũ update
    "gemini-2.0-flash-001",    # Bản thử nghiệm (Experimental)
]

def setup_auth():
    """Thiết lập xác thực Google Cloud"""
    if os.path.exists(CREDENTIALS_FILE):
        print(f"🔑 Đã tìm thấy file key: {CREDENTIALS_FILE}")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_FILE
        return True
    else:
        # Nếu đang chạy trên Cloud (Render/Railway) thì có thể nó tự nhận diện qua biến môi trường
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            print("☁️ Đang dùng Key từ biến môi trường Server.")
            return True
        print("❌ LỖI: Không tìm thấy file 'credentials.json'!")
        print("👉 Vui lòng tải JSON Key từ Google Cloud Console -> IAM -> Service Accounts.")
        return False

def test_vertex_model(model_name):
    print(f"🔄 Testing: {model_name:<25} ... ", end="")
    try:
        model = GenerativeModel(model_name)
        # Gửi request test
        response = model.generate_content("Hello Vertex AI")
        
        if response.text:
            print("✅ OK")
            return True
    except NotFound:
        print("❌ Không tồn tại (Not Found)")
    except PermissionDenied:
        print("⛔ Không có quyền (Cần bật Vertex AI API)")
    except ResourceExhausted:
        print("⚠️ Hết Quota (Server bận)")
    except Exception as e:
        print(f"❌ Lỗi: {str(e)[:50]}")
    return False

def main():
    print("="*60)
    print(f"☁️  KIỂM TRA KẾT NỐI VERTEX AI - PROJECT: {PROJECT_ID}")
    print("="*60)

    if not setup_auth():
        return

    try:
        # Khởi tạo Vertex AI SDK
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        print(f"✅ Kết nối thành công tới Region: {LOCATION}")
    except Exception as e:
        print(f"❌ Lỗi khởi tạo Vertex AI: {e}")
        return

    print("\n🚀 BẮT ĐẦU TEST MODEL:")
    print("-" * 60)
    
    working_list = []
    
    for m in VERTEX_MODELS_TO_TEST:
        if test_vertex_model(m):
            working_list.append(m)
        time.sleep(0.5) # Nghỉ xíu

    print("-" * 60)
    if working_list:
        print(f"\n🎉 CÁC MODEL BẠN CÓ THỂ DÙNG VỚI PROJECT {PROJECT_ID}:")
        for w in working_list:
            print(f"   🌟 {w}")
            
        print("\n👉 Hãy copy tên model này vào file rag_core.py (biến MODEL_NAME)")
    else:
        print("\n❌ Không có model nào chạy được. Hãy kiểm tra lại:")
        print("1. Đã bật 'Vertex AI API' trong Google Cloud Console chưa?")
        print("2. Service Account có quyền 'Vertex AI User' chưa?")

if __name__ == "__main__":
    main()