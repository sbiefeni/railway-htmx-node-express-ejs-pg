FROM python:3.9-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    libopenblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install models first so face_recognition finds them on import
RUN pip install --no-cache-dir git+https://github.com/ageitgey/face_recognition_models

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scan.py .

CMD ["python", "-u", "scan.py"]
