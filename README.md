# NanoPress Backend

FastAPI server that handles PDF compression server-side using Ghostscript and OCRmyPDF.
Drop-in replacement for the browser-side pdf.js / pdf-lib / tesseract.js pipeline.

**Speed improvement over browser-side:**
| Task              | Browser (old)  | Server (new)  |
|-------------------|---------------|---------------|
| Compress 50-page PDF | 30–90 s   | 1–4 s         |
| OCR 20-page scan  | 4–8 min       | 15–45 s       |
| 100 MB batch      | often crashes | handled fine  |

---

## Prerequisites

### macOS (Homebrew)
```bash
brew install ghostscript
brew install ocrmypdf        # pulls in tesseract automatically
pip3 install -r requirements.txt
```

### Ubuntu / Debian
```bash
sudo apt-get install ghostscript ocrmypdf tesseract-ocr \
  tesseract-ocr-spa tesseract-ocr-fra tesseract-ocr-deu \
  tesseract-ocr-ita tesseract-ocr-por tesseract-ocr-nld \
  tesseract-ocr-chi-sim tesseract-ocr-jpn tesseract-ocr-ara \
  tesseract-ocr-kor tesseract-ocr-rus
pip3 install -r requirements.txt
```

### Docker (recommended for production)
```bash
docker build -t nanopress-backend .
docker run -p 8000:8000 nanopress-backend
```

---

## Running

**Development:**
```bash
uvicorn main:app --reload --port 8000
```

**Production:**
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
```

Check it's running:
```bash
curl http://localhost:8000/health
# {"status":"ok","ghostscript":true,"version":"1.0.0"}
```

---

## API

### POST /compress

Accepts a `multipart/form-data` request.

| Field        | Type    | Default   | Description                          |
|--------------|---------|-----------|--------------------------------------|
| `file`       | file    | required  | The PDF to compress                  |
| `mode`       | string  | `safe`    | `safe` / `max` / `balanced`          |
| `quality`    | float   | `0.75`    | 0.30 (smallest) → 0.95 (best)        |
| `ocr`        | bool    | `false`   | Add OCR text layer (max mode only)   |
| `ocr_lang`   | string  | `eng`     | Tesseract language code              |
| `page_range` | string  | `""`      | e.g. `1-5,8,11-20` (empty = all)     |
| `title`      | string  | `""`      | PDF metadata: document title         |
| `author`     | string  | `""`      | PDF metadata: author                 |
| `subject`    | string  | `""`      | PDF metadata: subject / description  |
| `keywords`   | string  | `""`      | PDF metadata: keywords               |

**Response:** `application/pdf` binary with these custom headers:

| Header               | Value                                 |
|----------------------|---------------------------------------|
| `X-Original-Size`    | Original file size in bytes           |
| `X-Compressed-Size`  | Compressed file size in bytes         |
| `X-Reduction-Pct`    | Percentage reduction e.g. `62.3`      |
| `X-Pages`            | Page count                            |
| `X-Actual-Mode`      | Which mode actually ran (`safe`/`max`)|
| `X-OCR-Ran`          | `true` / `false`                      |
| `X-Has-Text`         | `true` if text layer detected         |

**Example (curl):**
```bash
curl -X POST http://localhost:8000/compress \
  -F "file=@report.pdf" \
  -F "mode=safe" \
  -F "quality=0.82" \
  -o report_compressed.pdf \
  -D -
```

---

## Compression modes

| Mode       | Ghostscript preset | Image DPI | What it preserves             |
|------------|-------------------|-----------|-------------------------------|
| `safe`     | `/printer`        | 150       | text, fonts, links, vectors   |
| `max`      | `/ebook`–`/screen`| 72–150    | images (lower quality)        |
| `balanced` | auto-routed       | 96–150    | routes to safe or max         |

Balanced auto-detects: text-heavy PDFs → safe path. Image-heavy / scanned → max path.

---

## File size limits

Default: 500 MB. Change `MAX_FILE_BYTES` in `main.py`.

Synchronous processing is fine up to ~200 pages before users notice latency.
For larger batches or concurrent users, add Redis + RQ workers next.

---

## Frontend integration

The frontend HTML calls `POST /compress` and reads result headers.
Set `API_BASE` in the HTML to point at this server:

```javascript
const API_BASE = 'http://localhost:8000';  // dev
// const API_BASE = 'https://api.yourdomain.com';  // prod
```

See the `nanopress-server.html` file for the full integration.

---

## Security notes

- Ghostscript is run with `-dSAFER` which disables file system access from within PDFs
- Temp files are cleaned up after each request via FastAPI background tasks
- For public-facing deployments: add rate limiting (e.g. `slowapi`) and file validation
- OCRmyPDF docs warn against using it as a public upload service with untrusted PDFs —
  consider sandboxing workers (Docker, gVisor) for fully public use
