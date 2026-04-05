"""
compress.py — PDF compression pipeline

Three tools, each doing what it does best:
  PyMuPDF   → analysis, page extraction, page count
  Ghostscript → structural compression, image downsampling, stream rewriting
  OCRmyPDF  → OCR text layer + its own Ghostscript compression pass

Routing:
  safe     → Ghostscript /ebook, no aggressive downsampling, keep text/links
  max      → Ghostscript /screen or /ebook depending on quality slider
             + OCRmyPDF when ocr=True
  balanced → PyMuPDF analyses first 5 pages; routes to safe or max
"""

import os
import subprocess
from pathlib import Path
from typing import Optional


# ── PyMuPDF (fitz) ──────────────────────────────────────────────────────────
try:
    import fitz  # PyMuPDF
    FITZ_OK = True
except ImportError:
    FITZ_OK = False


# ── Page range parsing ────────────────────────────────────────────────────────

def parse_page_range(range_str: str, total: int) -> list[int]:
    """
    Parse '1-5,8,11-20' into [1,2,3,4,5,8,11,...,20].
    Returns all pages if range_str is empty or 'all'.
    """
    s = (range_str or "").strip()
    if not s or s.lower() == "all":
        return list(range(1, total + 1))

    pages: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a.strip()), int(b.strip())
            for i in range(min(lo, hi), min(max(lo, hi), total) + 1):
                if i >= 1:
                    pages.add(i)
        else:
            n = int(part)
            if 1 <= n <= total:
                pages.add(n)

    return sorted(pages) if pages else list(range(1, total + 1))


# ── PDF analysis ──────────────────────────────────────────────────────────────

def get_pdf_info(path: str) -> dict:
    """
    Return page count and content-type hints using PyMuPDF.
    Falls back gracefully when PyMuPDF isn't installed.
    """
    if not FITZ_OK:
        return {"pages": 0, "has_text": True, "has_images": False}

    doc = fitz.open(path)
    info = {
        "pages": doc.page_count,
        "has_text": False,
        "has_images": False,
    }
    # Sample first 5 pages — enough to decide routing
    for i in range(min(5, doc.page_count)):
        page = doc[i]
        if len(page.get_text().strip()) > 30:
            info["has_text"] = True
        if page.get_images():
            info["has_images"] = True
        if info["has_text"] and info["has_images"]:
            break  # enough information

    doc.close()
    return info


# ── Page extraction ───────────────────────────────────────────────────────────

def extract_page_range(input_path: str, page_range: str, output_path: str) -> str:
    """
    Extract only the requested pages into a new PDF using PyMuPDF.
    Returns output_path. Falls back to input_path if PyMuPDF missing.
    """
    if not FITZ_OK:
        return input_path  # Ghostscript will handle the whole file

    doc   = fitz.open(input_path)
    pages = parse_page_range(page_range, doc.page_count)

    out = fitz.open()
    for p in pages:
        out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
    out.save(output_path)
    doc.close()
    out.close()
    return output_path


# ── Ghostscript settings ──────────────────────────────────────────────────────

def _gs_settings(mode: str, quality: float) -> dict:
    """
    Map mode + quality slider (0.30–0.95) to Ghostscript flags.

    Ghostscript presets for reference:
      /screen  → 72 dpi  images, heavy compression
      /ebook   → 150 dpi images, moderate compression
      /printer → 300 dpi images, light compression (preserves detail)
      /default → similar to /printer
    """
    # JPEG quality: 0.30 quality → ~42, 0.95 quality → ~90
    jpeg_q = int(20 + quality * 73)

    if mode == "safe":
        # Preserve text, fonts, links; only compress image streams
        return {
            "preset":    "/printer",
            "dpi":       150,
            "jpeg_q":    max(jpeg_q, 80),   # never go below 80 in safe mode
            "downsample": False,
        }
    elif mode == "max":
        if quality >= 0.80:
            return {"preset": "/ebook",  "dpi": 150, "jpeg_q": jpeg_q, "downsample": True}
        elif quality >= 0.55:
            return {"preset": "/screen", "dpi":  96, "jpeg_q": jpeg_q, "downsample": True}
        else:
            return {"preset": "/screen", "dpi":  72, "jpeg_q": max(jpeg_q, 25), "downsample": True}
    else:
        # balanced  — will have been auto-routed before reaching here,
        # but keep a sensible default
        return {"preset": "/ebook", "dpi": 96, "jpeg_q": jpeg_q, "downsample": True}


# ── Ghostscript runner ────────────────────────────────────────────────────────

def run_ghostscript(input_path: str, output_path: str, mode: str, quality: float) -> None:
    """
    Compress a PDF with Ghostscript.
    Raises RuntimeError if Ghostscript fails.
    Raises FileNotFoundError if 'gs' is not on PATH.
    """
    s = _gs_settings(mode, quality)

    cmd = [
        "gs",
        "-dBATCH", "-dNOPAUSE", "-dQUIET", "-dSAFER",
        "-sDEVICE=pdfwrite",
        f"-dPDFSETTINGS={s['preset']}",
        "-dCompatibilityLevel=1.7",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        f"-dJPEGQ={s['jpeg_q']}",
    ]

    if s["downsample"]:
        dpi = s["dpi"]
        cmd += [
            f"-dColorImageResolution={dpi}",
            f"-dGrayImageResolution={dpi}",
            f"-dMonoImageResolution={min(dpi * 2, 600)}",
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dDownsampleColorImages=true",
            "-dDownsampleGrayImages=true",
        ]

    cmd += [f"-sOutputFile={output_path}", input_path]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        # Truncate to last 500 chars — GS can produce very verbose errors
        err = (result.stderr or "").strip()[-500:]
        raise RuntimeError(f"Ghostscript error:\n{err}")


# ── OCRmyPDF runner ───────────────────────────────────────────────────────────

def run_ocrmypdf(
    input_path: str,
    output_path: str,
    quality: float,
    lang: str,
) -> None:
    """
    Run OCRmyPDF to add a searchable text layer and compress.

    OCRmyPDF exit codes:
      0  = success
      6  = already contains text (we treat this as success)
    """
    # --optimize 1/2/3 controls how aggressively OCRmyPDF recompresses images
    optimize = 3 if quality < 0.55 else (2 if quality < 0.80 else 1)

    cmd = [
        "ocrmypdf",
        "--quiet",
        "--optimize", str(optimize),
        "-l", lang,
        "--output-type", "pdf",
        "--pdfa-image-compression", "jpeg",
        input_path,
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode not in (0, 6):
        err = (result.stderr or "").strip()[-500:]
        raise RuntimeError(f"OCRmyPDF error:\n{err}")

    # If OCRmyPDF somehow made the file larger (rare), do an extra GS pass
    if (
        os.path.exists(output_path)
        and os.path.getsize(output_path) > os.path.getsize(input_path) * 0.95
    ):
        tmp = output_path + ".gs2.pdf"
        run_ghostscript(output_path, tmp, "max", quality)
        os.replace(tmp, output_path)


# ── PDF metadata writer ───────────────────────────────────────────────────────

def apply_metadata(pdf_path: str, meta: dict) -> None:
    """
    Write title/author/subject/keywords into the PDF using PyMuPDF.
    No-op if PyMuPDF not available or all meta values are empty.
    """
    if not FITZ_OK:
        return
    if not any(meta.values()):
        return

    doc = fitz.open(pdf_path)
    current = doc.metadata

    updated = {
        "title":    meta.get("title")    or current.get("title", ""),
        "author":   meta.get("author")   or current.get("author", ""),
        "subject":  meta.get("subject")  or current.get("subject", ""),
        "keywords": meta.get("keywords") or current.get("keywords", ""),
        "creator":  "NanoPress",
        "producer": "NanoPress",
    }

    doc.set_metadata(updated)

    # Save to a temp file alongside the original, then replace
    tmp = pdf_path + ".meta.pdf"
    doc.save(tmp, incremental=False, deflate=True)
    doc.close()
    os.replace(tmp, pdf_path)


# ── Main entry point ──────────────────────────────────────────────────────────

def compress_pdf(
    input_path:  str,
    output_path: str,
    mode:        str   = "safe",
    quality:     float = 0.75,
    ocr:         bool  = False,
    ocr_lang:    str   = "eng",
    page_range:  str   = "",
    metadata:    Optional[dict] = None,
    tmpdir:      str   = "",
) -> dict:
    """
    Full compression pipeline. Returns a stats dict with sizes, page count,
    which mode actually ran, and whether OCR was applied.

    Steps:
      1. Get PDF info (page count, text/image content type)
      2. Extract page range if specified
      3. Route 'balanced' to 'safe' or 'max' based on content
      4. Compress (Ghostscript or OCRmyPDF)
      5. Apply metadata
    """
    original_size = os.path.getsize(input_path)

    # 1. Analyse
    info = get_pdf_info(input_path)

    # 2. Extract pages
    working = input_path
    if page_range and page_range.strip():
        page_tmp = os.path.join(tmpdir or os.path.dirname(input_path), "pages.pdf")
        working  = extract_page_range(input_path, page_range, page_tmp)

    # 3. Route balanced
    actual_mode = mode
    if mode == "balanced":
        # Text-heavy with no images → safe path preserves everything
        # Image-heavy / scanned → max path handles it better
        actual_mode = (
            "safe" if (info["has_text"] and not info["has_images"])
            else "max"
        )

    # 4. Compress
    ocr_ran = False
    if ocr and actual_mode == "max":
        run_ocrmypdf(working, output_path, quality, ocr_lang)
        ocr_ran = True
    else:
        run_ghostscript(working, output_path, actual_mode, quality)

    # 5. Metadata
    if metadata:
        apply_metadata(output_path, metadata)

    compressed_size = os.path.getsize(output_path)
    reduction       = (1 - compressed_size / original_size) * 100 if original_size else 0

    return {
        "original_size":   original_size,
        "compressed_size": compressed_size,
        "reduction_pct":   round(reduction, 1),
        "pages":           info["pages"],
        "actual_mode":     actual_mode,
        "ocr_ran":         ocr_ran,
        "has_text":        info["has_text"],
    }
