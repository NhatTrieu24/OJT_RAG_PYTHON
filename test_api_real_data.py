import requests
import time
import json
import sys

# ================== CẤU HÌNH ==================
URL = "http://127.0.0.1:8000/chat"

class BColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'      # Màu cho SQL
    OKCYAN = '\033[96m'      # Màu cho VECTOR
    OKGREEN = '\033[92m'     # Passed
    WARNING = '\033[93m'
    FAIL = '\033[91m'        # Failed
    ENDC = '\033[0m'
    BOLD = '\033[1m'

# ================== TEST CASES (ĐÃ GÁN NHÃN) ==================
test_cases = [

    # ===== NHÓM 1: CÔNG TY (THƯỜNG LÀ SQL VÌ DỮ LIỆU CẤU TRÚC) =====
    {
        "id": "COMP_01",
        "type": "SQL",  # Truy vấn cột Address chính xác
        "name": "Địa chỉ FPT Software",
        "question": "Địa chỉ của FPT Software ở đâu?",
        "expected_all": ["FPT", "Hà Nội"]
    },
    {
        "id": "COMP_02",
        "type": "SQL",  # Truy vấn cột Website
        "name": "Website Viettel",
        "question": "Website chính thức của Viettel là gì?",
        "expected_any": ["viettel.com.vn"]
    },
    {
        "id": "COMP_03",
        "type": "SQL",  # Truy vấn cột Email (Có Fuzzy matching)
        "name": "Email MoMo (sai chính tả)",
        "question": "Email liên hệ của môm là gì?",
        "expected_any": ["@momo", "momo.vn"]
    },
    {
        "id": "COMP_04",
        "type": "SQL",  # Truy vấn cột TaxCode
        "name": "Mã số thuế VNG",
        "question": "Mã số thuế của VNG Corporation?",
        "expected_any": ["0100", "03049"]
    },

    # ===== NHÓM 2: JOB (HỖN HỢP) =====
    {
        "id": "JOB_01",
        "type": "VECTOR", # Tìm kiếm ngữ nghĩa (Job nào phù hợp với Software Engineer?)
        "name": "Tìm job Software Engineer",
        "question": "Có job nào cho Software Engineer không?",
        "expected_any": ["Software", "Developer", "Intern", "Kỹ sư"]
    },
    {
        "id": "JOB_02",
        "type": "SQL",    # Truy vấn cột Salary
        "name": "Lương job Software Engineer",
        "question": "Mức lương của Software Engineer Intern?",
        "expected_any": ["USD", "-", "salary", "thỏa thuận", "triệu"]
    },
    {
        "id": "JOB_03",
        "type": "VECTOR", # Nội dung yêu cầu công việc (Văn bản dài)
        "name": "Yêu cầu job",
        "question": "Yêu cầu của vị trí Software Engineer Intern là gì?",
        "expected_any": ["C#", ".NET", "knowledge", "kinh nghiệm"]
    },
    {
        "id": "JOB_04",
        "type": "SQL",    # Truy vấn cột Location
        "name": "Địa điểm làm việc",
        "question": "Vị trí Software Engineer làm việc ở đâu?",
        "expected_any": ["Hanoi", "Hà Nội", "HCM", "Ho Chi Minh"]
    },

    # ===== NHÓM 3: HỌC KỲ / NGÀNH (SQL) =====
    {
        "id": "SEM_01",
        "type": "SQL",    # Liệt kê danh sách
        "name": "Danh sách kỳ học",
        "question": "Hệ thống hiện có những kỳ học nào?",
        "expected_any": ["Spring", "Fall", "Summer"]
    },
    {
        "id": "SEM_02",
        "type": "SQL",    # Truy vấn ngày tháng cụ thể
        "name": "Ngày bắt đầu Spring 2025",
        "question": "Kỳ Spring 2025 bắt đầu khi nào?",
        "expected_any": ["01/01/2025", "tháng 1"]
    },
    {
        "id": "MAJOR_01",
        "type": "SQL",    # Truy vấn mã chính xác
        "name": "Mã ngành An toàn thông tin",
        "question": "Mã ngành An toàn thông tin là gì?",
        "expected_any": ["INFOSEC", "IA"]
    },

    # ===== NHÓM 4: TÀI LIỆU (VECTOR) =====
    {
        "id": "DOC_01",
        "type": "VECTOR", # Tìm trong kho vector document
        "name": "Tìm tài liệu Test Doc (Không tồn tại)",
        "question": "Có tài liệu nào tên Test Doc không?",
        "expected_any": ["Không", "không tìm thấy", "chưa có"]
    },
    {
        "id": "DOC_02",
        "type": "VECTOR", # Tìm trong kho vector document
        "name": "Tìm tài liệu Handbook",
        "question": "Có tài liệu nào tên Handbook không?",
        "expected_any": ["Handbook", "Company Handbook", "sổ tay"]
    },

    # ===== NHÓM 5: TỔNG HỢP / ADMIN (SQL NÂNG CAO) =====
    {
        "id": "ADV_01",
        "type": "SQL",    # Join bảng Company + Job
        "name": "Danh sách job của FPT",
        "question": "FPT Software đang tuyển những vị trí nào?",
        "expected_any": ["Intern", "Engineer", "Developer", "Fresher"]
    },
    {
        "id": "ADV_02",
        "type": "SQL",    # Hàm COUNT(*)
        "name": "Đếm sinh viên",
        "question": "Hiện có bao nhiêu sinh viên trong hệ thống?",
        "expected_any": ["sinh viên", "người", "user", "1", "2", "3"] # Giả sử số lượng là số nhỏ
    }
]

# ================== RUN TEST ==================
def run_tests():
    print(f"\n{BColors.HEADER}=== STARTING RAG SYSTEM TEST (SQL vs VECTOR) ==={BColors.ENDC}\n")
    print(f"Target URL: {URL}")
    passed = 0
    total = len(test_cases)

    # Check server
    try:
        requests.get(URL.replace("/chat", "/docs"), timeout=5)
    except requests.exceptions.ConnectionError:
        print(f"{BColors.FAIL}❌ LỖI: Backend không chạy! Hãy start server trước.{BColors.ENDC}")
        return

    for idx, case in enumerate(test_cases):
        # Hiển thị Type với màu sắc riêng biệt
        type_str = f"[{case['type']}]"
        if case['type'] == "SQL":
            type_colored = f"{BColors.OKBLUE}{BColors.BOLD}{type_str: <8}{BColors.ENDC}"
        else:
            type_colored = f"{BColors.OKCYAN}{BColors.BOLD}{type_str: <8}{BColors.ENDC}"

        print(f"🔹 {type_colored} Test [{case['id']}]: {case['name']}")
        
        payload = {"question": case["question"]}
        
        try:
            res = requests.post(URL, data=payload, timeout=60) # timeout lâu hơn cho Vector

            if res.status_code == 200:
                data = res.json()
                ans = data.get("answer", "")
                sql = data.get("sql_debug", "N/A")

                print(f"   🤖 Answer: {ans.strip()}")
                
                # Logic hiển thị debug
                if case['type'] == "SQL":
                    if sql != "N/A" and sql is not None:
                        print(f"   🛠  SQL Used: {sql}")
                    else:
                        print(f"   ⚠️  {BColors.WARNING}Warning: Expected SQL but got none.{BColors.ENDC}")
                
                ans_lower = ans.lower()
                passed_flag = False

                if "expected_all" in case:
                    passed_flag = all(k.lower() in ans_lower for k in case["expected_all"])
                elif "expected_any" in case:
                    passed_flag = any(k.lower() in ans_lower for k in case["expected_any"])

                if passed_flag:
                    print(f"   {BColors.OKGREEN}✅ PASSED{BColors.ENDC}")
                    passed += 1
                else:
                    print(f"   {BColors.FAIL}❌ FAILED{BColors.ENDC}")
                    print(f"      Expected: {case.get('expected_all') or case.get('expected_any')}")

            else:
                print(f"   ❌ HTTP Error {res.status_code}: {res.text}")

        except requests.exceptions.Timeout:
            print(f"   ❌ Timeout: Server xử lý quá lâu (>60s)")
        except Exception as e:
            print(f"   ❌ Error: {e}")

        # Delay
        if idx < total - 1:
            print("   ⏳ Waiting 15s (Google Rate Limit)...", end="\r")
            time.sleep(15)
            print(" " * 60, end="\r")

    print(f"\n" + "="*40)
    print(f"🎓 RESULT: {passed}/{total} PASSED")
    
    if passed == total:
        print(f"{BColors.OKGREEN}🎉 SYSTEM PERFECT!{BColors.ENDC}")

if __name__ == "__main__":
    run_tests()