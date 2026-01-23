# 1. Dùng Python 3.11
FROM python:3.11

# 2. Tạo user non-root (Bắt buộc trên Hugging Face)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# 3. Thiết lập thư mục làm việc
WORKDIR $HOME/app

# 4. Copy file requirements và cài đặt
COPY --chown=user requirements.txt $HOME/app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 5. Copy toàn bộ code vào
COPY --chown=user . $HOME/app

# 6. Mở cổng 7860 (Cổng mặc định của Hugging Face Spaces)
EXPOSE 7860

# 7. Lệnh chạy Server
# Lưu ý: Phải set host 0.0.0.0 và port 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
