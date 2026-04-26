"""
Claude-based slide deck generation — alternative to NotebookLM.

Generates structured slide JSON via Claude Sonnet 4.6, converts to PPTX
using the existing generate_powerpoint module, creates preview PNGs,
and generates voiceover scripts.
"""

import os
import json
import requests
import subprocess
import shutil

from generate_powerpoint import generate_powerpoint_file
from generate_notebooklm import (
    _pdf_to_images,
    _extract_slide_texts,
    _clean_voiceover_script,
    _parse_voiceover_scripts,
)


ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-sonnet-4-6'


def _call_claude(prompt, max_tokens=16000):
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise Exception('ANTHROPIC_API_KEY not configured')

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        },
        json={
            'model': MODEL,
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
        },
        timeout=300,
    )
    if not resp.ok:
        error_body = resp.text[:500]
        print(f'[Claude Slides] API error ({resp.status_code}): {error_body}')
        try:
            err_json = resp.json()
            err_msg = err_json.get('error', {}).get('message', error_body)
        except Exception:
            err_msg = error_body
        raise Exception(f'Claude API error ({resp.status_code}): {err_msg}')
    data = resp.json()
    print(f'[Claude Slides] API call completed: {data.get("usage", {})}')
    return data['content'][0]['text']


def _pptx_to_pdf(pptx_path, output_dir):
    """Convert PPTX to PDF using LibreOffice headless. Returns PDF path or None on failure."""
    try:
        subprocess.run(
            ['soffice', '--headless', '--convert-to', 'pdf', '--outdir', output_dir, pptx_path],
            check=True, timeout=120, capture_output=True,
        )
        base = os.path.splitext(os.path.basename(pptx_path))[0]
        pdf_path = os.path.join(output_dir, f'{base}.pdf')
        if os.path.exists(pdf_path):
            return pdf_path
        print('[Claude Slides] LibreOffice produced no PDF output')
        return None
    except Exception as e:
        print(f'[Claude Slides] PPTX→PDF conversion failed: {e}')
        return None


def generate_slide_deck_claude(documents, title, output_dir, options=None):
    """
    Generate a slide deck using Claude instead of NotebookLM.

    Args:
        documents: List of dicts with 'filename' and 'extracted_text'.
        title: Presentation title.
        output_dir: Directory for output files.
        options: Dict with slide_format, slide_length, slide_count, target_time,
                 max_time, instructions, ppt_theme, ppt_template_url, etc.

    Returns:
        (None, pdf_path, pptx_path, slide_image_paths, voiceover_scripts)
        notebook_id is None (no NotebookLM notebook created).
    """
    options = options or {}
    os.makedirs(output_dir, exist_ok=True)

    slide_format = options.get('slide_format', 'DETAILED_DECK')
    slide_count = options.get('slide_count')
    target_time = options.get('target_time')
    max_time = options.get('max_time')
    instructions = options.get('instructions', '')
    theme_id = options.get('ppt_theme', 'corporate')
    template_url = options.get('ppt_template_url')

    # ── Phase 1: Generate slide content via Claude ──────────────────────────
    doc_text = '\n\n---\n\n'.join(
        f'Document: {d["filename"]}\n{d.get("extracted_text", "")}'
        for d in documents
    )

    format_instruction = (
        'Create detailed slides with comprehensive bullet points and supporting detail.'
        if slide_format == 'DETAILED_DECK'
        else 'Create concise presenter slides with key talking points only.'
    )

    count_instruction = f'Create exactly {slide_count} slides (including title and summary).' if slide_count else 'Create 8-15 slides as appropriate for the content.'

    timing_instruction = ''
    if target_time:
        timing_instruction = f'The presentation should be designed for approximately {target_time} minutes of narration.'

    user_instruction = f'\nAdditional instructions: {instructions}' if instructions else ''

    safe_title = (title or 'Training Presentation').replace('"', '\\"')
    prompt = f"""You are an expert instructional designer creating a professional training presentation.

Based on the source documents below, create a slide deck titled "{safe_title}".

--- SOURCE DOCUMENTS ---
{doc_text}

--- REQUIREMENTS ---
{format_instruction}
{count_instruction}
{timing_instruction}
{user_instruction}

Return ONLY valid JSON in this exact format (no markdown, no commentary):
{{
  "title": "{safe_title}",
  "slides": [
    {{
      "type": "title",
      "title": "{safe_title}",
      "content": "{safe_title}"
    }},
    {{
      "type": "content",
      "title": "Slide Title Here",
      "bullets": ["Key point 1", "Key point 2", "Key point 3"]
    }},
    {{
      "type": "content",
      "title": "Another Slide",
      "bullets": ["Point A", "Point B"]
    }},
    {{
      "type": "summary",
      "title": "Summary",
      "bullets": ["Key takeaway 1", "Key takeaway 2"]
    }}
  ]
}}

Rules:
- First slide must be type "title" with the presentation title
- Last slide should be type "summary" with key takeaways
- All other slides should be type "content" with a title and bullets array
- Each bullet should be a complete, informative sentence
- Content must be grounded in the source documents — do not invent facts
- Organise content logically with clear section progression"""

    print(f'[Claude Slides] Generating slide content for "{title}" ({len(documents)} docs, ~{len(doc_text)} chars)')
    response_text = _call_claude(prompt)

    # Parse JSON from response
    json_start = response_text.find('{')
    json_end = response_text.rfind('}') + 1
    if json_start == -1 or json_end == 0:
        raise Exception('Claude did not return valid JSON for slides')
    course_data = json.loads(response_text[json_start:json_end])

    slide_count_actual = len(course_data.get('slides', []))
    print(f'[Claude Slides] Generated {slide_count_actual} slides')

    # ── Phase 2: Generate PPTX ─────────────────────────────────────────────
    template_path = None
    if template_url:
        template_path = os.path.join(output_dir, 'template.pptx')
        try:
            tmpl_resp = requests.get(template_url, timeout=30)
            tmpl_resp.raise_for_status()
            raw_path = os.path.join(output_dir, 'template_raw')
            with open(raw_path, 'wb') as f:
                f.write(tmpl_resp.content)
            # Convert .potx to .pptx — python-pptx rejects template content type
            import zipfile
            if zipfile.is_zipfile(raw_path):
                with zipfile.ZipFile(raw_path, 'r') as zin:
                    content_type_xml = zin.read('[Content_Types].xml').decode('utf-8')
                    if 'presentationml.template' in content_type_xml:
                        content_type_xml = content_type_xml.replace(
                            'presentationml.template.main+xml',
                            'presentationml.presentation.main+xml'
                        )
                        with zipfile.ZipFile(template_path, 'w') as zout:
                            for item in zin.infolist():
                                data = zin.read(item.filename)
                                if item.filename == '[Content_Types].xml':
                                    data = content_type_xml.encode('utf-8')
                                zout.writestr(item, data)
                        print('[Claude Slides] Converted .potx template to .pptx')
                    else:
                        shutil.copy2(raw_path, template_path)
            else:
                shutil.copy2(raw_path, template_path)
            os.remove(raw_path)
        except Exception as e:
            print(f'[Claude Slides] Failed to download/convert template: {e}')
            template_path = None

    pptx_path = os.path.join(output_dir, 'slides.pptx')
    generate_powerpoint_file(
        course_data,
        pptx_path,
        theme_id=theme_id,
        template_path=template_path,
    )
    print(f'[Claude Slides] PPTX generated: {pptx_path}')

    # ── Phase 3: Generate preview PNGs ─────────────────────────────────────
    pdf_path = _pptx_to_pdf(pptx_path, output_dir)
    slide_image_paths = []
    if pdf_path:
        try:
            slide_image_paths = _pdf_to_images(pdf_path, output_dir)
            print(f'[Claude Slides] {len(slide_image_paths)} preview PNGs generated')
        except Exception as e:
            print(f'[Claude Slides] PNG generation failed: {e}')
    else:
        print('[Claude Slides] Skipping preview PNGs (no PDF available)')

    # ── Phase 4: Generate voiceover scripts ────────────────────────────────
    slide_texts = _extract_slide_texts(pptx_path)
    num_slides = len(slide_texts)

    timing_prompt = ''
    if target_time:
        secs_per_slide = (target_time * 60) / max(num_slides, 1)
        timing_prompt = f"""
TIMING CONSTRAINT:
- Target total duration: {target_time} minutes
- Target per slide: ~{int(secs_per_slide)} seconds of narration
- Speaking rate: ~150 words per minute
- Each script should be approximately {int(secs_per_slide * 2.5)} words"""
        if max_time:
            max_secs = (max_time * 60) / max(num_slides, 1)
            timing_prompt += f'\n- MAXIMUM per slide: {int(max_secs)} seconds (~{int(max_secs * 2.5)} words). Do NOT exceed this.'

    slide_markers = '\n\n'.join(
        f'[SLIDE {i+1}]\n{text or "(title slide)"}'
        for i, text in enumerate(slide_texts)
    )

    vo_prompt = f"""You are writing voiceover narration scripts for a training presentation.

Below are the slides with their content. Write a natural, conversational narration script for EACH slide.

{slide_markers}
{timing_prompt}

RULES:
- Write one script per slide, prefixed with [SLIDE N]
- Only describe what is ON that specific slide — do not reference other slides
- Use natural, conversational language suitable for text-to-speech
- Do not include citations, reference numbers, document codes, or standards codes
- Do not use markdown formatting
- Match the slide order exactly

Return the scripts in this format:
[SLIDE 1]
Script for slide 1...

[SLIDE 2]
Script for slide 2...
"""

    print(f'[Claude Slides] Generating voiceover scripts for {num_slides} slides')
    vo_response = _call_claude(vo_prompt, max_tokens=8000)
    voiceover_scripts = _parse_voiceover_scripts(vo_response, num_slides)
    print(f'[Claude Slides] Generated {len([s for s in voiceover_scripts if s])} non-empty scripts')

    return None, pdf_path, pptx_path, slide_image_paths, voiceover_scripts
