import io
from pypdf import PdfReader
from docx import Document

async def extract_text_from_file(file, filename: str) -> str:
    """
    Hàm hỗ trợ đọc nội dung file.
    Không ảnh hưởng đến logic cũ của hệ thống.
    """
    try:
        content = await file.read()
        file_stream = io.BytesIO(content)
        text = ""

        if filename.lower().endswith(".pdf"):
            reader = PdfReader(file_stream)
            for page in reader.pages:
                text += page.extract_text() + "\n"
        
        elif filename.lower().endswith(".docx"):
            doc = Document(file_stream)
            for para in doc.paragraphs:
                text += para.text + "\n"
        else:
            return "Lỗi: Hệ thống chỉ hỗ trợ file PDF hoặc DOCX."

        return text.strip()
    except Exception as e:
        return f"Lỗi đọc file: {str(e)}"