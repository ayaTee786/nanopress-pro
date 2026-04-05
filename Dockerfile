FROM python:3.12-slim

# ── System tools ────────────────────────────────────────────────────────────
# ghostscript:  the actual compression engine
# ocrmypdf:     OCR + its own Ghostscript pass (pulls in Tesseract as a dep)
# tesseract-ocr-*: language packs for the 12 languages the frontend exposes
RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    ocrmypdf \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-ita \
    tesseract-ocr-por \
    tesseract-ocr-nld \
    tesseract-ocr-chi-sim \
    tesseract-ocr-jpn \
    tesseract-ocr-ara \
    tesseract-ocr-kor \
    tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ─────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── App ─────────────────────────────────────────────────────────────────────
COPY . .

EXPOSE 8000

# --workers 2: one per CPU core is fine; OCR+GS are CPU-bound per request
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
