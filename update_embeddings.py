import psycopg2
from agent_adk import get_query_embedding, DB_DSN, get_embeddings_batch

def sync_all_data():
    """Hàm này thực hiện đồng bộ toàn bộ bảng với logic Phẳng hóa"""
    print("🔄 [Update-System] Đang bắt đầu đồng bộ dữ liệu mới...")
    conn = None
    try:
        conn = psycopg2.connect(dsn=DB_DSN)
        cur = conn.cursor()
        
        # Ví dụ logic phẳng hóa cho Job Position
        sql_job = """
            SELECT jp.job_position_id, 
                   'Vị trí ' || COALESCE(jp.job_title, '') || ' tại ' || COALESCE(c.name, 'N/A')
            FROM job_position jp
            LEFT JOIN semester_company sc ON jp.semester_company_id = sc.semester_company_id
            LEFT JOIN company c ON sc.company_id = c.company_id
            WHERE jp.embedding IS NULL;
        """
        cur.execute(sql_job)
        rows = cur.fetchall()
        
        if rows:
            print(f"📦 Tìm thấy {len(rows)} dòng mới cần tạo Vector.")
            for row_id, text in rows:
                vector = get_query_embedding(text)
                if vector:
                    cur.execute("UPDATE job_position SET embedding = %s WHERE job_position_id = %s", (vector, row_id))
            conn.commit()
            print("✅ Cập nhật hoàn tất cho bảng Job Position.")
        else:
            print("✨ Không có dữ liệu mới cần cập nhật.")

    except Exception as e:
        print(f"❌ Lỗi khi cập nhật: {e}")
    finally:
        if conn: conn.close()