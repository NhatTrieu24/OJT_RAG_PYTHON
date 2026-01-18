import io
from pypdf import PdfReader
from docx import Document

async def extract_text_from_file(file, filename: str) -> str:
    """
    Hàm hỗ trợ đọc nội dung file PDF/DOCX từ bộ nhớ (UploadFile).
    Sử dụng pypdf và python-docx.
    """
    try:
        # 1. Đọc nội dung file vào bộ nhớ (Async read)
        content = await file.read()
        file_stream = io.BytesIO(content)
        text = ""

        # 2. Xử lý file PDF
        if filename.lower().endswith(".pdf"):
            try:
                reader = PdfReader(file_stream)
                for page in reader.pages:
                    # extract_text() an toàn hơn truy cập trực tiếp bbox
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
            except Exception as e:
                return f"Lỗi nội tại khi đọc PDF: {str(e)}"
        
        # 3. Xử lý file Word (DOCX)
        elif filename.lower().endswith(".docx"):
            try:
                doc = Document(file_stream)
                for para in doc.paragraphs:
                    text += para.text + "\n"
            except Exception as e:
                return f"Lỗi nội tại khi đọc DOCX: {str(e)}"
        
        else:
            return "Lỗi: Hệ thống chỉ hỗ trợ file PDF (.pdf) hoặc Word (.docx)."

        return text.strip()

    except Exception as e:
        return f"Lỗi không xác định khi mở file: {str(e)}"
