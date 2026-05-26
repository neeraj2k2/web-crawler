# syntax=docker/dockerfile:1
#
# Uses Microsoft's official Playwright Python base image which ships with
# Chromium + all OS-level dependencies pre-installed. This eliminates the
# `playwright install chromium --with-deps` step — previously the slowest
# layer (~20 min on Apple Silicon via QEMU emulation for linux/amd64).
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

COPY requirements.txt .

# Install CPU-only PyTorch first — the default pip install pulls the CUDA-enabled
# build (~1.5GB). We run on Cloud Run CPUs so CUDA is dead weight. CPU-only is ~250MB.
# Separate layer so it stays cached even when requirements.txt changes.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Bake models into the image at build time — avoids cold-start downloads.
# Separate layers so each model is cached independently.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
RUN python -m spacy download en_core_web_sm

# App code — changes most often, placed last so all above layers stay cached
COPY app/ ./app/

EXPOSE 8080

CMD ["gunicorn", "app.main:app", \
     "--workers", "2", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8080", \
     "--timeout", "300"]
