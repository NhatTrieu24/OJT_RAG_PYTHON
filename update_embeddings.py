import psycopg2
from agent_adk import get_query_embedding, DB_DSN

def sync():
    conn = psycopg2.connect(dsn=DB_DSN)
    cur = conn.cursor()
    
    # Danh sách các bảng cần cập nhật vector
    targets = [
        ("job_position", "job_title", "job_position_id"),
        ("ojtdocument", "title", "ojtdocument_id"),
        ("major", "major_title", "major_id")
    ]
    
    for table, col, id_col in targets:
        print(f"🔄 Đang cập nhật Vector cho bảng {table}...")
        cur.execute(f"SELECT {id_col}, {col} FROM {table} WHERE embedding IS NULL")
        rows = cur.fetchall()
        
        for row_id, text in rows:
            vector = get_query_embedding(text)
            if vector:
                cur.execute(f"UPDATE {table} SET embedding = %s WHERE {id_col} = %s", (vector, row_id))
        conn.commit()
    print("✅ Đã cập nhật xong toàn bộ Vector!")
    conn.close()

if __name__ == "__main__":
    sync()