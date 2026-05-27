FROM python:3.10-slim

# insightface/OpenCV headless deps
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download buffalo_l ArcFace model at build time so cold-start is fast.
# Models land in /root/.insightface/models/
ENV INSIGHTFACE_HOME=/root/.insightface
RUN python -c "\
from insightface.app import FaceAnalysis; \
app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider']); \
app.prepare(ctx_id=0, det_size=(640,640)); \
print('ArcFace model ready')"

COPY scan.py .

CMD ["python", "-u", "scan.py"]
