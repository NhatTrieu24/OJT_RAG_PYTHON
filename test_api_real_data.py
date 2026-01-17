import requests
import time
import json

# --- CẤU HÌNH ---
URL = "http://127.0.0.1:8000/chat"

class BColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

test_cases = [
    # --- NHÓM 1: THÔNG TIN CÔNG TY & HR (Dựa trên ID 1, 2) ---
    {
        "id": 1,
        "name": "Check HR Email (FPT Software)",
        "question": "Cho xin email HR của FPT Software?",
        # Data: hr@fptsoftware.com
        "expected": ["hr@fptsoftware.com", "HR FPT"] 
    },
    {
        "id": 2,
        "name": "Check Website Công ty (Viettel)",
        "question": "Website của Viettel là gì?",
        # Data: https://viettel.com.vn
        "expected": ["viettel.com.vn", "https"] 
    },

    # --- NHÓM 2: TRA CỨU VIỆC LÀM (JOB POSITION) ---
    {
        "id": 3,
        "name": "Check Lương Software Engineer (FPT)",
        "question": "Lương thực tập Software Engineer tại FPT bao nhiêu?",
        # Data: 500-700 USD
        "expected": ["500", "700", "USD", "đô"] 
    },
    {
        "id": 4,
        "name": "Check Job Bảo mật (Cybersecurity)",
        "question": "Có tuyển thực tập sinh mảng bảo mật không?",
        # Data: Cybersecurity Analyst Intern
        "expected": ["Cybersecurity", "Analyst", "Bảo mật", "có"]
    },
    {
        "id": 5,
        "name": "Check Job Design (Không có active job)",
        "question": "Có tuyển thiết kế đồ họa (Graphic Designer) kỳ này không?",
        # Data: Không có job Graphic Designer active trong bảng job_position
        "expected": ["không", "chưa", "không tìm thấy"]
    },

    # --- NHÓM 3: LOGIC HỌC KỲ (SEMESTER) ---
    {
        "id": 6,
        "name": "Check Học kỳ Active",
        "question": "Kỳ học nào đang diễn ra?",
        # Data: Spring 2025 (2025-01-01 -> 2025-04-30) is_active=false? 
        # Wait, check DB: Spring 2025 (ID 1) is_active=false, Fall 2025 (ID 3) is_active=true ???
        # À, trong dump: (3, 'Fall 2025', ..., true).
        "expected": ["Fall 2025", "Mùa thu 2025"]
    },

    # --- NHÓM 4: TÀI LIỆU (DOCUMENT) ---
    {
        "id": 7,
        "name": "Tìm Tài liệu OJT Guidelines",
        "question": "Tải OJT Guidelines ở đâu?",
        # Data: OJT Guidelines, ID 1
        "expected": ["OJT Guidelines", "link", "tải"]
    },

    # --- NHÓM 5: CROSS-LANGUAGE & SLANG ---
    {
        "id": 8,
        "name": "Trans: 'Lập trình viên' -> 'Software Engineer'",
        "question": "Tìm việc cho lập trình viên tại Hà Nội?",
        # Data: Software Engineer Intern (Location: Hanoi)
        "expected": ["Software Engineer", "Hanoi", "FPT"]
    },
    {
        "id": 9,
        "name": "Trans: 'An ninh mạng' -> 'Cybersecurity'",
        "question": "Lương thực tập an ninh mạng thế nào?",
        # Data: 600-800 USD
        "expected": ["600", "800", "USD"]
    }
]

def run_tests():
    print(f"\n{BColors.HEADER}{'='*25} REAL DATA DB VALIDATION {'='*25}{BColors.ENDC}\n")
    passed = 0
    
    for case in test_cases:
        print(f"{BColors.OKBLUE}Test #{case['id']} [{case['name']}]:{BColors.ENDC} {case['question']}")
        
        try:
            payload = {"question": case["question"]}
            start_time = time.time()
            res = requests.post(URL, data=payload, timeout=60)
            duration = time.time() - start_time
            
            if res.status_code == 200:
                data = res.json()
                answer = data.get('answer', '')
                sql_debug = data.get('sql_debug', 'N/A')
                
                print(f"🤖 Answer ({duration:.2f}s): {answer.strip()}")
                if sql_debug and sql_debug != 'N/A':
                    print(f"🛠  SQL Generated: {sql_debug}")

                # Logic Check (OR match)
                answer_lower = answer.lower()
                found_keywords = [k for k in case["expected"] if k.lower() in answer_lower]
                
                if found_keywords:
                    print(f"{BColors.OKGREEN}✅ PASSED (Matched: {found_keywords}){BColors.ENDC}")
                    passed += 1
                else:
                    print(f"{BColors.FAIL}❌ FAILED{BColors.ENDC}")
                    print(f"   Expected ANY of: {case['expected']}")
            else:
                print(f"{BColors.FAIL}❌ ERROR: HTTP {res.status_code}{BColors.ENDC}")
                print(res.text)
                
        except Exception as e:
            print(f"{BColors.FAIL}❌ EXCEPTION: {e}{BColors.ENDC}")
            
        print("-" * 60)
        time.sleep(1) 
        
    print(f"\n{BColors.HEADER}FINAL SCORE: {passed}/{len(test_cases)} ({int(passed/len(test_cases)*100)}%){BColors.ENDC}")

if __name__ == "__main__":
    run_tests()