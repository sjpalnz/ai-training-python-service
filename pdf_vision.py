"""
Vision-based PDF extraction.

Plain text extraction (pypdf) linearises a page, which destroys 2-D layout —
in particular wide spreadsheet/matrix PDFs (e.g. a PPE matrix exported from
Excel) lose the link between a row label and its cells, and scanned/image PDFs
yield no text at all. This module renders such pages to images and asks Claude
to transcribe them to Markdown, preserving tables so row<->column relationships
survive into the RAG index.

Gated by should_use_vision() so ordinary text PDFs are untouched. Rendering is
done with poppler's `pdftoppm` one tile (crop region) at a time, so peak memory
stays at roughly a single tile — no full-page raster is ever held in process.
This runs in a background thread (see api.py), never in the upload request.
"""
import base64
import gc
import os
import subprocess
import tempfile

import requests as http_requests

ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-sonnet-4-6'

# ── Heuristic thresholds ─────────────────────────────────────────────────────
# Standard pages: A4 = 595x842 pt, A3 = 842x1191 pt. Anything much larger is a
# spreadsheet/poster export (the PPE matrix is 2551x3609 pt).
WIDE_PAGE_PT = 1400
MIN_CHARS_PER_PAGE = 60      # below this average → likely scanned/image
MAX_VISION_PAGES = 20        # cost guard: don't vision huge documents

# ── Tiling / rendering ───────────────────────────────────────────────────────
DEFAULT_DPI = 130
MAX_EDGE = 1400              # max tile edge in px (Claude reads these well)
TILE_OVERLAP = 120
MAX_TILES_PER_PAGE = 16
OVERVIEW_EDGE = 1300        # long edge (px) of the low-res whole-page overview


def _page_sizes(pdf_path):
    """Per-page (width_pt, height_pt) using pypdf."""
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    out = []
    for p in reader.pages:
        box = p.mediabox
        out.append((float(box.width), float(box.height)))
    return out


def should_use_vision(pdf_path, pypdf_text):
    """
    Decide whether a PDF needs vision extraction.
    Returns (bool, reason). Vision is used for oversized (matrix/spreadsheet)
    pages or text-sparse (scanned) pages, unless disabled or too many pages.
    """
    if os.environ.get('PDF_VISION_ENABLED', 'true').lower() == 'false':
        return False, 'disabled via PDF_VISION_ENABLED'
    try:
        sizes = _page_sizes(pdf_path)
    except Exception:
        sizes = []
    n = max(1, len(sizes))
    if n > MAX_VISION_PAGES:
        return False, f'too many pages ({n} > {MAX_VISION_PAGES})'
    if any(w > WIDE_PAGE_PT or h > WIDE_PAGE_PT for (w, h) in sizes):
        return True, 'oversized page (likely spreadsheet/matrix)'
    if len((pypdf_text or '').strip()) < MIN_CHARS_PER_PAGE * n:
        return True, f'sparse text ({len((pypdf_text or "").strip())} chars / {n} pages)'
    return False, 'plain text sufficient'


def _pdftoppm(pdf_path, page, dpi, crop=None):
    """Render one page (or a crop region) to PNG bytes via poppler pdftoppm.
    crop = (x, y, w, h) in pixels at the given dpi. Low, bounded memory."""
    with tempfile.TemporaryDirectory() as d:
        prefix = os.path.join(d, 'out')
        cmd = ['pdftoppm', '-png', '-singlefile', '-r', str(int(round(dpi))),
               '-f', str(page), '-l', str(page)]
        if crop:
            x, y, w, h = crop
            cmd += ['-x', str(int(x)), '-y', str(int(y)), '-W', str(int(w)), '-H', str(int(h))]
        cmd += [pdf_path, prefix]
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        with open(prefix + '.png', 'rb') as f:
            return f.read()


def _tile_regions(px_w, px_h):
    """Return [(x, y, w, h)] px tiles each <= MAX_EDGE, with overlap."""
    px_w, px_h = int(px_w), int(px_h)
    if max(px_w, px_h) <= MAX_EDGE:
        return [(0, 0, px_w, px_h)]
    step = MAX_EDGE - TILE_OVERLAP
    regions = []
    y = 0
    while True:
        x = 0
        while True:
            regions.append((x, y, min(MAX_EDGE, px_w - x), min(MAX_EDGE, px_h - y)))
            if x + MAX_EDGE >= px_w:
                break
            x += step
        if y + MAX_EDGE >= px_h:
            break
        y += step
    return regions


def _b64(data):
    return base64.standard_b64encode(data).decode('ascii')


_PROMPT = (
    "You are transcribing ONE page of a document into Markdown for a search index.\n"
    "- Reproduce ALL text faithfully; do not summarise, omit, or invent anything.\n"
    "- Render every table as a GitHub-flavoured Markdown table, keeping each row aligned "
    "with its column headers so the relationship between a row label and each cell is preserved.\n"
    "- The page may be supplied as a low-res OVERVIEW image first (for overall layout), "
    "followed by high-resolution TILES in reading order (left-to-right, then top-to-bottom) "
    "that overlap slightly. Reconstruct the single continuous page; do NOT duplicate content "
    "that appears in the overlap between tiles.\n"
    "- Output ONLY the Markdown for this page, with no commentary."
)


def _transcribe(b64_images, api_key):
    content = [{'type': 'text', 'text': _PROMPT}]
    for data in b64_images:
        content.append({
            'type': 'image',
            'source': {'type': 'base64', 'media_type': 'image/png', 'data': data},
        })
    resp = http_requests.post(
        ANTHROPIC_URL,
        headers={'Content-Type': 'application/json', 'x-api-key': api_key, 'anthropic-version': '2023-06-01'},
        json={'model': MODEL, 'max_tokens': 8000, 'messages': [{'role': 'user', 'content': content}]},
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = [b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text']
    return '\n'.join(parts).strip()


def extract_pdf_markdown(pdf_path, api_key=None):
    """Render the PDF (tile-by-tile via pdftoppm) and transcribe each page to
    Markdown via Claude. Raises on hard failure. Memory stays ~one tile."""
    api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not set')

    sizes = _page_sizes(pdf_path)
    num_pages = min(len(sizes) or 1, MAX_VISION_PAGES)

    out = []
    for i in range(1, num_pages + 1):
        w_pt, h_pt = sizes[i - 1] if i - 1 < len(sizes) else (612.0, 792.0)
        dpi = float(DEFAULT_DPI)
        regions = _tile_regions(w_pt / 72.0 * dpi, h_pt / 72.0 * dpi)
        # Lower the render dpi until the page fits the tile budget (grid rounding
        # means one scale step isn't always enough — iterate to convergence).
        while len(regions) > MAX_TILES_PER_PAGE and dpi > 40.0:
            dpi = max(40.0, dpi * (MAX_TILES_PER_PAGE / float(len(regions))) ** 0.5 * 0.97)
            regions = _tile_regions(w_pt / 72.0 * dpi, h_pt / 72.0 * dpi)

        images_b64 = []
        if len(regions) > 1:
            ov_dpi = max(10.0, OVERVIEW_EDGE / (max(w_pt, h_pt) / 72.0))
            images_b64.append(_b64(_pdftoppm(pdf_path, i, ov_dpi)))
        for (x, y, w, h) in regions:
            images_b64.append(_b64(_pdftoppm(pdf_path, i, dpi, crop=(x, y, w, h))))
            gc.collect()

        md = _transcribe(images_b64, api_key)
        del images_b64
        gc.collect()
        if md:
            out.append(md)
    return '\n\n---\n\n'.join(out).strip()
