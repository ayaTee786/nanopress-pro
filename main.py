"""
NanoPress Backend — Path A minimal server
FastAPI + Ghostscript + OCRmyPDF + PyMuPDF

Accepts a PDF upload, compresses it server-side, returns the result
with metadata in response headers. No queue, no DB — synchronous is
fine for files up to ~200 pages. Add RQ later when you need it.
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import tempfile, os, shutil
from pathlib import Path

from compress import compress_pdf

import os

app = FastAPI(title="NanoPress API", version="1.0.0")

# In production set ALLOWED_ORIGINS env var to your Netlify URL:
#   ALLOWED_ORIGINS=https://your-app.netlify.app
# Locally defaults to * so any origin works during development.
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "X-Original-Size", "X-Compressed-Size", "X-Reduction-Pct",
        "X-Pages", "X-Actual-Mode", "X-OCR-Ran", "X-Has-Text",
    ],
)

MAX_FILE_BYTES = 500 * 1024 * 1024  # 500 MB


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Used by the frontend to detect whether the server is available."""
    import subprocess
    gs_ok = subprocess.run(["gs", "--version"], capture_output=True).returncode == 0
    return {
        "status": "ok",
        "ghostscript": gs_ok,
        "version": "1.0.0",
    }


# ── Main compress endpoint ────────────────────────────────────────────────────

@app.post("/compress")
async def compress(
    background_tasks: BackgroundTasks,
    file:       UploadFile = File(...),
    mode:       str   = Form(default="safe"),     # safe | max | balanced
    quality:    float = Form(default=0.75),        # 0.30 – 0.95
    ocr:        bool  = Form(default=False),
    ocr_lang:   str   = Form(default="eng"),
    page_range: str   = Form(default=""),          # e.g. "1-5,8,11-20"
    title:      str   = Form(default=""),
    author:     str   = Form(default=""),
    subject:    str   = Form(default=""),
    keywords:   str   = Form(default=""),
):
    # ── Validate inputs ───────────────────────────────────────────────────────
    fname = file.filename or ""
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(400, detail="File must be a PDF (.pdf extension)")

    if mode not in ("safe", "max", "balanced"):
        raise HTTPException(400, detail="mode must be: safe | max | balanced")

    quality = max(0.30, min(0.95, quality))

    # ── Read upload ───────────────────────────────────────────────────────────
    content = await file.read()

    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, detail=f"File too large — max {MAX_FILE_BYTES // 1048576} MB")

    if len(content) < 100:
        raise HTTPException(400, detail="File appears empty or corrupt")

    # ── Work in a temp directory, clean up in background after response ───────
    tmpdir = tempfile.mkdtemp(prefix="nanopress_")
    background_tasks.add_task(shutil.rmtree, tmpdir, ignore_errors=True)

    input_path  = os.path.join(tmpdir, "input.pdf")
    output_path = os.path.join(tmpdir, "output.pdf")

    with open(input_path, "wb") as f:
        f.write(content)

    # ── Compress ──────────────────────────────────────────────────────────────
    meta = {"title": title, "author": author, "subject": subject, "keywords": keywords}
    try:
        stats = compress_pdf(
            input_path=input_path,
            output_path=output_path,
            mode=mode,
            quality=quality,
            ocr=ocr,
            ocr_lang=ocr_lang,
            page_range=page_range,
            metadata=meta,
            tmpdir=tmpdir,
        )

    except FileNotFoundError as exc:
        raise HTTPException(500, detail=f"Server tool not found: {exc}. Is Ghostscript installed?")

    except RuntimeError as exc:
        raise HTTPException(422, detail=str(exc))

    except Exception as exc:
        raise HTTPException(500, detail=f"Compression failed: {exc}")

    # ── Return compressed file + metadata headers ─────────────────────────────
    out_name = Path(fname).stem + "_compressed.pdf"

    return FileResponse(
        path=output_path,
        media_type="application/pdf",
        filename=out_name,
        headers={
            "X-Original-Size":   str(stats["original_size"]),
            "X-Compressed-Size": str(stats["compressed_size"]),
            "X-Reduction-Pct":   str(stats["reduction_pct"]),
            "X-Pages":           str(stats["pages"]),
            "X-Actual-Mode":     stats["actual_mode"],
            "X-OCR-Ran":         "true" if stats["ocr_ran"] else "false",
            "X-Has-Text":        "true" if stats["has_text"] else "false",
        },
    )
