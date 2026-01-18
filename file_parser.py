import io
import pdfplumber
from docx import Document
from fastapi import UploadFile
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

async def extract_text_from_file(file: UploadFile, filename: str) -> str:
    try:
        content = await file.read()
        text = ""

        # ===== PDF =====
        if filename.lower().endswith(".pdf"):
            try:
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
            except Exception as e:
                return f"Lỗi đọc PDF (pdfplumber): {str(e)}"

        # ===== DOCX =====
        elif filename.lower().endswith(".docx"):
            try:
                doc = Document(io.BytesIO(content))
                for para in doc.paragraphs:
                    text += para.text + "\n"
            except Exception as e:
                return f"Lỗi đọc DOCX: {str(e)}"

        else:
            return "Lỗi: Chỉ hỗ trợ PDF và DOCX"

        if not text.strip():
            return "Không trích xuất được nội dung từ file"

        return text.strip()

    except Exception as e:
        return f"Lỗi hệ thống: {str(e)}"
