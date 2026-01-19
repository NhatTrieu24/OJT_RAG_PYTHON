import requests
import time
import json

# ================== CẤU HÌNH ==================
URL = "http://127.0.0.1:8000/chat" 

class BColors:
    HEADER = '\033[95m'
    OKCYAN = '\033[96m'      # Màu cho VECTOR
    OKGREEN = '\033[92m'     # Passed
    FAIL = '\033[91m'        # Failed
    ENDC = '\033[0m'
    BOLD = '\033[1m'

# ================== TEST CASES ĐA BẢNG (REAL DATA) ==================
test_cases = [
    # NHÓM 1: DOANH NGHIỆP & CÔNG VIỆC (Liên kết Job - Company)
    {
        "id": "RAG_01",
        "name": "Tuyển dụng MoMo",
        "question": "Momo đang tuyển vị trí nào và lương bao nhiêu?",
        "expected_any": ["Illustrator", "Cybersecurity", "100", "1000", "9000"]
    },
    
    # NHÓM 2: TÀI LIỆU OJT (Bảng ojtdocument)
    {
        "id": "DOC_01",
        "name": "Link tài liệu MSB",
        "question": "Cho tôi xin link tài liệu của ngân hàng MSB",
        "expected_any": ["drive.google.com", "MSB", "NGÂN HÀNG"]
    },
    {
        "id": "DOC_02",
        "name": "Tài liệu HTV",
        "question": "Thông tin về tài liệu của đài truyền hình HTV",
        "expected_any": ["HTV", "ĐÀI TRUYỀN HÌNH", "drive.google.com"]
    },

    # NHÓM 3: THÔNG TIN SINH VIÊN & VAI TRÒ (Bảng User)
    {
        "id": "USER_01",
        "name": "MSSV Teresttt",
        "question": "Sinh viên Teresttt có mã số sinh viên là gì?",
        "expected_any": ["S11000", "Teresttt"]
    },
    {
        "id": "USER_02",
        "name": "Vai trò Recruiter",
        "question": "Recruiter MoMo đóng vai trò gì trong hệ thống?",
        "expected_any": ["company", "tuyển dụng"]
    },

    # NHÓM 4: KỲ HỌC & THỜI GIAN (Bảng semester)
    {
        "id": "SEM_01",
        "name": "Thời gian kỳ Spring 2025",
        "question": "Kỳ Spring 2025 bắt đầu khi nào?",
        "expected_any": ["2025-01-01", "tháng 1"]
    },

    # NHÓM 5: CHUYÊN NGÀNH (Bảng major - Kiểm tra dịch thuật)
    {
        "id": "MAJ_01",
        "name": "Mô tả Digital Marketing",
        "question": "Ngành Digital Marketing học về cái gì?",
        "expected_any": ["online marketing", "SEO", "truyền thông", "tiếp thị", "phân tích"]
    }
]

# ================== RUN TEST ==================
def run_tests():
    print(f"\n{BColors.HEADER}=== STARTING MULTI-TABLE RAG VALIDATION ==={BColors.ENDC}\n")
    passed = 0
    total = len(test_cases)

    for idx, case in enumerate(test_cases):
        type_colored = f"{BColors.OKCYAN}{BColors.BOLD}[VECTOR]{BColors.ENDC}"
        print(f"🔹 {type_colored} Test [{case['id']}]: {case['name']}")
        
        payload = {"question": case["question"]}
        
        try:
            res = requests.post(URL, data=payload, timeout=60)

            if res.status_code == 200:
                data = res.json()
                ans = data.get("answer", "")
                
                display_ans = ans.strip().replace('\n', ' ')
                print(f"   🤖 AI: {display_ans[:150]}...") 
                
                ans_lower = ans.lower()
                passed_flag = any(k.lower() in ans_lower for k in case["expected_any"])

                if passed_flag:
                    print(f"   {BColors.OKGREEN}✅ PASSED{BColors.ENDC}")
                    passed += 1
                else:
                    print(f"   {BColors.FAIL}❌ FAILED{BColors.ENDC}")
                    print(f"      Mong đợi chứa một trong: {case['expected_any']}")
            else:
                print(f"   ❌ Error {res.status_code}")

        except Exception as e:
            print(f"   ❌ Network Error: {e}")

        if idx < total - 1:
            time.sleep(5) # Giảm xuống 5s vì đã tối ưu context phẳng

    print(f"\n" + "="*50)
    print(f"{BColors.BOLD}🎓 TỔNG KẾT: {passed}/{total} CASES THÀNH CÔNG{BColors.ENDC}")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_tests()