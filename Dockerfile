# Dockerfile
FROM python:3.11-slim

# Tạo working dir
WORKDIR /app

# Copy requirements
COPY requirements.txt /app/requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl && \
    python -m pip install --upgrade pip && \
    pip install -r /app/requirements.txt && \
    apt-get remove -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY . /app

# Entry point sẽ xét credential từ SECRET env (xem phần SECRET)
ENTRYPOINT ["python", "main.py"]
