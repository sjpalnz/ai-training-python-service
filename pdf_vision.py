"""
Vision-based PDF extraction.

Plain text extraction (pypdf) linearises a page, which destroys 2-D layout —
in particular wide spreadsheet/matrix PDFs (e.g. a PPE matrix exported from
Excel) lose the link between a row label and its cells, and scanned/image PDFs
yield no text at all. This module renders such pages to images and asks Claude
to transcribe them to Markdown, preserving tables so row<->column relationships
survive into the RAG index.

It is used as a *fallback* from the normal pypdf path, gated by should_use_vision()
so ordinary text PDFs are untouched (no added cost/latency).
"""
import base64
import gc
import io
import os

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
# Kept deliberately modest: extraction runs inside the web request, so a smaller
# image set keeps the Claude call well under the gunicorn worker timeout and the
# container memory budget.
DEFAULT_DPI = 120
MAX_EDGE = 1400             # max tile edge in px (Claude reads these well)
TILE_OVERLAP = 120
MAX_TILES_PER_PAGE = 16
OVERVIEW_EDGE = 1300        # downscaled whole-page image for layout reference


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


def _resize_to_edge(img, max_edge):
    w, h = img.size
    if max(w, h) <= max_edge:
        return img
    scale = max_edge / float(max(w, h))
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))))


def _tile_image(img):
    """Split an image into overlapping tiles each <= MAX_EDGE px."""
    w, h = img.size
    if max(w, h) <= MAX_EDGE:
        return [img]
    step = MAX_EDGE - TILE_OVERLAP
    tiles = []
    y = 0
    while True:
        x = 0
        while True:
            tiles.append(img.crop((x, y, min(x + MAX_EDGE, w), min(y + MAX_EDGE, h))))
            if x + MAX_EDGE >= w:
                break
            x += step
        if y + MAX_EDGE >= h:
            break
        y += step
    return tiles


def _img_to_b64(img):
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.standard_b64encode(buf.getvalue()).decode('ascii')


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


def _page_to_b64_images(page):
    """Build the base64 image set for one page, freeing PIL objects as we go to
    keep peak memory low. Returns a list of base64 PNG strings."""
    tiles = _tile_image(page)
    if len(tiles) > MAX_TILES_PER_PAGE:
        scale = (MAX_TILES_PER_PAGE / float(len(tiles))) ** 0.5
        smaller = page.resize((max(1, int(page.size[0] * scale)), max(1, int(page.size[1] * scale))))
        for t in tiles:
            t.close()
        tiles = _tile_image(smaller)
        page = smaller

    if len(tiles) > 1:
        # Prepend a downscaled overview to aid reconstruction of the tiled page.
        overview = _resize_to_edge(page, OVERVIEW_EDGE)
        b64 = [_img_to_b64(overview)]
        overview.close()
        for t in tiles:
            b64.append(_img_to_b64(t))
            t.close()
    else:
        b64 = [_img_to_b64(tiles[0])]
        tiles[0].close()
    return b64


def extract_pdf_markdown(pdf_path, api_key=None):
    """Render the PDF and transcribe each page to Markdown via Claude. Raises on hard failure.
    Renders one page at a time to bound peak memory."""
    api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not set')

    from pdf2image import convert_from_path, pdfinfo_from_path
    try:
        num_pages = int(pdfinfo_from_path(pdf_path).get('Pages', 1))
    except Exception:
        num_pages = 1
    num_pages = min(num_pages, MAX_VISION_PAGES)

    out = []
    for i in range(1, num_pages + 1):
        rendered = convert_from_path(pdf_path, dpi=DEFAULT_DPI, first_page=i, last_page=i)
        page = rendered[0]
        b64_images = _page_to_b64_images(page)
        page.close()
        gc.collect()
        md = _transcribe(b64_images, api_key)
        del b64_images
        gc.collect()
        if md:
            out.append(md)
    return '\n\n---\n\n'.join(out).strip()
