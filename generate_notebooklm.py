"""
NotebookLM integration for generating podcasts, infographics, videos, and
slide decks via notebooklm-py.

This module wraps the notebooklm-py library to create temporary notebooks,
add course content as sources, generate audio/infographic/video/slide-deck
artifacts, and download the results.

Requires NOTEBOOKLM_AUTH_JSON env var with Google session state.
"""
import asyncio
import os


def _get_event_loop():
    """Get or create an event loop for running async code from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


async def _get_or_create_notebook_async(client, title, source_text, existing_notebook_id=None):
    """Return the notebook ID to use for generation.

    If existing_notebook_id is provided and the notebook is still alive,
    reuse it (adding the source only if it has none yet).  Otherwise create
    a fresh notebook and add the source text.
    """
    if existing_notebook_id:
        try:
            await client.notebooks.get(existing_notebook_id)   # raises if deleted
            sources = await client.sources.list(existing_notebook_id)
            if sources:
                print(f"[NotebookLM] Reusing notebook {existing_notebook_id} ({len(sources)} source(s) already present)")
                return existing_notebook_id
            # Notebook exists but source was never added — add it now
            print(f"[NotebookLM] Reusing notebook {existing_notebook_id}, adding source")
            await client.sources.add_text(existing_notebook_id, title, source_text[:50000], wait=True, wait_timeout=180.0)
            return existing_notebook_id
        except Exception as e:
            print(f"[NotebookLM] Existing notebook {existing_notebook_id} not usable ({e}), creating new one")

    nb = await client.notebooks.create(f"Course: {title}")
    print(f"[NotebookLM] Created new notebook {nb.id}")
    await client.sources.add_text(nb.id, title, source_text[:50000], wait=True, wait_timeout=180.0)
    return nb.id


async def _generate_podcast_async(source_text, storyboard_json, output_path, options=None, existing_notebook_id=None):
    """Create (or reuse) a NotebookLM notebook, generate podcast, download MP3."""
    from notebooklm import NotebookLMClient
    from notebooklm.rpc.types import AudioFormat, AudioLength

    opts = options or {}
    title = storyboard_json.get('title', 'Course Content')

    # Map string values from frontend to enums
    fmt_map = {
        'DEEP_DIVE': AudioFormat.DEEP_DIVE,
        'BRIEF':     AudioFormat.BRIEF,
        'CRITIQUE':  AudioFormat.CRITIQUE,
        'DEBATE':    AudioFormat.DEBATE,
    }
    len_map = {
        'SHORT':   AudioLength.SHORT,
        'DEFAULT': AudioLength.DEFAULT,
        'LONG':    AudioLength.LONG,
    }
    audio_format = fmt_map.get(opts.get('format', ''), AudioFormat.DEEP_DIVE)
    audio_length = len_map.get(opts.get('length', ''), AudioLength.DEFAULT)

    default_instructions = (
        f"Create an engaging educational podcast about '{title}'. "
        "Make it conversational and suitable for learning. "
        "Cover the key concepts thoroughly."
    )
    instructions = opts.get('instructions') or default_instructions

    async with await NotebookLMClient.from_storage() as client:
        notebook_id = await _get_or_create_notebook_async(client, title, source_text, existing_notebook_id)

        try:
            # Generate audio podcast
            status = await client.artifacts.generate_audio(
                notebook_id,
                instructions=instructions,
                audio_format=audio_format,
                audio_length=audio_length,
            )
            # Allow up to 15 minutes — audio generation is slow
            final = await client.artifacts.wait_for_completion(notebook_id, status.task_id, timeout=900.0)

            # Check if NotebookLM itself reported a failure
            if final.is_failed:
                if final.is_rate_limited:
                    raise Exception('NotebookLM rate limit exceeded — please try again later')
                raise Exception(f'NotebookLM audio generation failed: {final.error or "unknown error"}')

            # Download the generated audio
            await client.artifacts.download_audio(notebook_id, output_path)

            return notebook_id

        except Exception:
            # NOTE: not deleting notebook on failure — keep it for inspection
            raise


async def _generate_infographic_async(source_text, storyboard_json, output_path, options=None, existing_notebook_id=None):
    """Create (or reuse) a NotebookLM notebook, generate infographic, download PNG."""
    from notebooklm import NotebookLMClient
    from notebooklm.rpc.types import InfographicOrientation, InfographicDetail

    opts = options or {}
    title = storyboard_json.get('title', 'Course Content')

    ori_map = {
        'LANDSCAPE': InfographicOrientation.LANDSCAPE,
        'PORTRAIT':  InfographicOrientation.PORTRAIT,
        'SQUARE':    InfographicOrientation.SQUARE,
    }
    det_map = {
        'CONCISE':  InfographicDetail.CONCISE,
        'STANDARD': InfographicDetail.STANDARD,
        'DETAILED': InfographicDetail.DETAILED,
    }
    # Only pass explicit values when user chose non-default; otherwise pass None
    # so the API uses its own defaults (passing PORTRAIT/STANDARD enums can
    # trigger USER_DISPLAYABLE_ERROR on some account configurations).
    orientation  = ori_map.get(opts.get('orientation', ''))   # None if not specified
    detail_level = det_map.get(opts.get('detail', ''))        # None if not specified
    instructions = opts.get('instructions') or None

    async with await NotebookLMClient.from_storage() as client:
        notebook_id = await _get_or_create_notebook_async(client, title, source_text, existing_notebook_id)

        try:
            # Generate infographic
            print(f"[NotebookLM] Calling generate_infographic: notebook={notebook_id}, orientation={orientation}, detail_level={detail_level}, instructions={bool(instructions)}")
            status = await client.artifacts.generate_infographic(
                notebook_id,
                instructions=instructions,
                orientation=orientation,
                detail_level=detail_level,
            )
            print(f"[NotebookLM] generate_infographic returned status: task_id={getattr(status, 'task_id', None)}, status={getattr(status, 'status', None)}, error={getattr(status, 'error', None)}")
            # Fast-fail: if the API rejected the request immediately, don't poll
            if getattr(status, 'is_failed', False) or not getattr(status, 'task_id', None):
                raise Exception(f'NotebookLM infographic generation rejected: {getattr(status, "error", None) or "no task_id returned"}')
            # Allow up to 15 minutes — infographic generation can be slow
            final = await client.artifacts.wait_for_completion(notebook_id, status.task_id, timeout=900.0)

            # Check if NotebookLM itself reported a failure
            if final.is_failed:
                if final.is_rate_limited:
                    raise Exception('NotebookLM rate limit exceeded — please try again later')
                raise Exception(f'NotebookLM infographic generation failed: {final.error or "unknown error"}')

            # Download the generated infographic
            await client.artifacts.download_infographic(notebook_id, output_path)

            return notebook_id

        except Exception:
            # NOTE: not deleting notebook on failure — keep it for inspection
            raise


async def _generate_video_async(source_text, storyboard_json, output_path, options=None, existing_notebook_id=None):
    """Create (or reuse) a NotebookLM notebook, generate video, download MP4."""
    from notebooklm import NotebookLMClient
    from notebooklm.rpc.types import VideoFormat, VideoStyle

    opts = options or {}
    title = storyboard_json.get('title', 'Course Content')

    fmt_map = {
        'EXPLAINER': VideoFormat.EXPLAINER,
        'BRIEF':     VideoFormat.BRIEF,
    }
    sty_map = {
        'AUTO_SELECT': VideoStyle.AUTO_SELECT,
        'WHITEBOARD':  VideoStyle.WHITEBOARD,
        'CLASSIC':     VideoStyle.CLASSIC,
        'KAWAII':      VideoStyle.KAWAII,
        'ANIME':       VideoStyle.ANIME,
        'WATERCOLOR':  VideoStyle.WATERCOLOR,
        'RETRO_PRINT': VideoStyle.RETRO_PRINT,
        'HERITAGE':    VideoStyle.HERITAGE,
        'PAPER_CRAFT': VideoStyle.PAPER_CRAFT,
    }
    video_format = fmt_map.get(opts.get('format', ''), VideoFormat.EXPLAINER)
    video_style  = sty_map.get(opts.get('style', ''),  VideoStyle.AUTO_SELECT)

    default_instructions = (
        f"Create an engaging educational video overview of '{title}'. "
        "Make it clear, informative, and suitable for learning. "
        "Cover the key concepts thoroughly."
    )
    instructions = opts.get('instructions') or default_instructions

    async with await NotebookLMClient.from_storage() as client:
        notebook_id = await _get_or_create_notebook_async(client, title, source_text, existing_notebook_id)

        try:
            status = await client.artifacts.generate_video(
                notebook_id,
                instructions=instructions,
                video_format=video_format,
                video_style=video_style,
            )
            # Allow up to 30 minutes — video generation is slower than podcast/infographic
            final = await client.artifacts.wait_for_completion(notebook_id, status.task_id, timeout=1800.0)

            # Check if NotebookLM itself reported a failure
            if final.is_failed:
                if final.is_rate_limited:
                    raise Exception('NotebookLM rate limit exceeded — please try again later')
                raise Exception(f'NotebookLM video generation failed: {final.error or "unknown error"}')

            # Download the generated video
            await client.artifacts.download_video(notebook_id, output_path)

            return notebook_id

        except Exception:
            # NOTE: not deleting notebook on failure — keep it for inspection
            raise


def _pdf_to_images(pdf_path, output_dir):
    """Convert each page of a PDF to a PNG image. Returns list of image file paths."""
    from pdf2image import convert_from_path
    images = convert_from_path(pdf_path, dpi=200, fmt='png')
    paths = []
    for i, img in enumerate(images):
        img_path = os.path.join(output_dir, f'slide_{i+1}.png')
        img.save(img_path, 'PNG')
        paths.append(img_path)
    return paths


async def _generate_slide_deck_async(source_text, title, output_dir, options=None, existing_notebook_id=None):
    """Create (or reuse) a NotebookLM notebook, generate slide deck, download PDF + PPTX + per-slide PNGs."""
    from notebooklm import NotebookLMClient
    from notebooklm.rpc.types import SlideDeckFormat, SlideDeckLength

    opts = options or {}

    fmt_map = {
        'DETAILED_DECK':    SlideDeckFormat.DETAILED_DECK,
        'PRESENTER_SLIDES': SlideDeckFormat.PRESENTER_SLIDES,
    }
    len_map = {
        'DEFAULT': SlideDeckLength.DEFAULT,
        'SHORT':   SlideDeckLength.SHORT,
    }
    slide_format = fmt_map.get(opts.get('slide_format', ''))
    slide_length = len_map.get(opts.get('slide_length', ''))
    instructions = opts.get('instructions') or None

    async with await NotebookLMClient.from_storage() as client:
        notebook_id = await _get_or_create_notebook_async(client, title, source_text, existing_notebook_id)

        try:
            print(f"[NotebookLM] Generating slide deck: notebook={notebook_id}, format={slide_format}, length={slide_length}")
            status = await client.artifacts.generate_slide_deck(
                notebook_id,
                instructions=instructions,
                slide_format=slide_format,
                slide_length=slide_length,
            )
            if getattr(status, 'is_failed', False) or not getattr(status, 'task_id', None):
                raise Exception(f'NotebookLM slide deck generation rejected: {getattr(status, "error", None) or "no task_id returned"}')

            # Allow up to 15 minutes
            final = await client.artifacts.wait_for_completion(notebook_id, status.task_id, timeout=900.0)

            if final.is_failed:
                if final.is_rate_limited:
                    raise Exception('NotebookLM rate limit exceeded — please try again later')
                raise Exception(f'NotebookLM slide deck generation failed: {final.error or "unknown error"}')

            # Download as PDF (for preview images)
            pdf_path = os.path.join(output_dir, 'slides.pdf')
            await client.artifacts.download_slide_deck(notebook_id, pdf_path)

            # Download as PPTX (for final download)
            pptx_path = os.path.join(output_dir, 'slides.pptx')
            try:
                await client.artifacts.download_slide_deck(notebook_id, pptx_path, format='pptx')
            except Exception as e:
                print(f"[NotebookLM] PPTX download failed ({e}), will use PDF only")
                pptx_path = None

            # Convert PDF pages to individual PNGs for preview
            slide_image_paths = _pdf_to_images(pdf_path, output_dir)
            print(f"[NotebookLM] Slide deck generated: {len(slide_image_paths)} slides")

            return notebook_id, pdf_path, pptx_path, slide_image_paths

        except Exception:
            raise


async def _cleanup_notebook_async(notebook_id):
    """Delete a temporary NotebookLM notebook."""
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        await client.notebooks.delete(notebook_id)


async def _check_auth_async():
    """Test NotebookLM auth by listing notebooks."""
    from notebooklm import NotebookLMClient

    async with await NotebookLMClient.from_storage() as client:
        await client.notebooks.list()
    return True


# ── Public sync wrappers ──────────────────────────────────────────────────────

def generate_podcast(source_text, storyboard_json, output_path, options=None, existing_notebook_id=None):
    """Sync wrapper: generate a podcast MP3 from course content."""
    loop = _get_event_loop()
    return loop.run_until_complete(
        _generate_podcast_async(source_text, storyboard_json, output_path, options, existing_notebook_id)
    )


def generate_infographic(source_text, storyboard_json, output_path, options=None, existing_notebook_id=None):
    """Sync wrapper: generate an infographic PNG from course content."""
    loop = _get_event_loop()
    return loop.run_until_complete(
        _generate_infographic_async(source_text, storyboard_json, output_path, options, existing_notebook_id)
    )


def generate_video(source_text, storyboard_json, output_path, options=None, existing_notebook_id=None):
    """Sync wrapper: generate a video MP4 from course content."""
    loop = _get_event_loop()
    return loop.run_until_complete(
        _generate_video_async(source_text, storyboard_json, output_path, options, existing_notebook_id)
    )


def generate_slide_deck(source_text, title, output_dir, options=None, existing_notebook_id=None):
    """Sync wrapper: generate a slide deck PDF + PPTX + preview images from content."""
    loop = _get_event_loop()
    return loop.run_until_complete(
        _generate_slide_deck_async(source_text, title, output_dir, options, existing_notebook_id)
    )


def cleanup_notebook(notebook_id):
    """Sync wrapper: delete a temporary NotebookLM notebook."""
    loop = _get_event_loop()
    loop.run_until_complete(_cleanup_notebook_async(notebook_id))


def check_auth():
    """Sync wrapper: test whether NotebookLM auth is valid. Returns True or raises."""
    loop = _get_event_loop()
    return loop.run_until_complete(_check_auth_async())
