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

# ================== TEST CASES THỰC TẾ ==================
test_cases = [

    # ===== NHÓM 1: THÔNG TIN DOANH NGHIỆP =====
    {
        "id": "COMP_01",
        "name": "Địa chỉ FPT Software",
        "question": "Văn phòng của FPT Software nằm ở đâu vậy?",
        "expected_any": ["Tố Hữu", "Hà Nội", "Hòa Lạc", "Quận 9", "Công nghệ cao"]
    },
    {
        "id": "COMP_02",
        "name": "Website MoMo",
        "question": "Trang web của MoMo là gì?",
        "expected_any": ["momo.vn"]
    },

    # ===== NHÓM 2: CÔNG VIỆC & KỸ NĂNG =====
    {
        "id": "JOB_01",
        "name": "Tìm job .NET",
        "question": "Có vị trí thực tập .NET nào không?",
        "expected_any": [".NET", "C#", "Software", "Intern", "Backend"]
    },
    {
        "id": "JOB_02",
        "name": "Yêu cầu kỹ năng React",
        "question": "Thực tập ReactJS thì cần những gì?",
        "expected_any": ["Javascript", "React", "Tailwind", "HTML", "CSS"]
    },
    {
        "id": "JOB_03",
        "name": "Mức lương hỗ trợ",
        "question": "Lương hỗ trợ cho thực tập sinh BackEnd là bao nhiêu?",
        "expected_any": ["5tr", "5.000.000", "4.000.000", "thỏa thuận", "VNĐ"]
    },

    # ===== NHÓM 3: TÀI LIỆU OJT (PDF) =====
    # {
    #     "id": "DOC_01",
    #     "name": "Thời gian OJT",
    #     "question": "Kỳ thực tập OJT thường kéo dài bao lâu?",
    #     "expected_any": ["14", "15", "tuần", "tháng", "học kỳ"]
    # },
    # {
    #     "id": "DOC_02",
    #     "name": "Báo cáo thực tập",
    #     "question": "Sinh viên có phải nộp báo cáo hàng tuần không?",
    #     "expected_any": ["báo cáo", "weekly", "hàng tuần", "quy định", "nộp"]
    # },

    # ===== NHÓM 4: KỲ HỌC & NGÀNH HỌC =====
    {
        "id": "SEM_01",
        "name": "Kỳ Spring 2025",
        "question": "Khi nào thì bắt đầu kỳ Spring 2025?",
        "expected_any": ["01/01/2025", "tháng 1", "2025"]
    },
    {
        "id": "MAJOR_01",
        "name": "Ngành Software Engineering",
        "question": "Ngành Software Engineering học về cái gì?",
        "expected_any": ["phần mềm", "hệ thống", "software", "phát triển"]
    }
]

# ================== RUN TEST ==================
def run_tests():
    print(f"\n{BColors.HEADER}=== STARTING REAL-DATA RAG TEST ==={BColors.ENDC}\n")
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
                
                print(f"   🤖 AI: {ans.strip()[:150]}...") # In ngắn gọn
                
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
            print(f"   ❌ Error: {e}")

        # Rate Limit
        if idx < total - 1:
            time.sleep(8)

    print(f"\n" + "="*40)
    print(f"🎓 KẾT QUẢ: {passed}/{total} THÀNH CÔNG")

if __name__ == "__main__":
    run_tests()