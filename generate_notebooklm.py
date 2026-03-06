"""
NotebookLM integration for generating podcasts and infographics via notebooklm-py.

This module wraps the notebooklm-py library to create temporary notebooks,
add course content as sources, generate audio/infographic artifacts, and
download the results. Temporary notebooks are cleaned up after generation.

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


async def _generate_podcast_async(source_text, storyboard_json, output_path):
    """Create a NotebookLM notebook, add source text, generate podcast, download MP3."""
    from notebooklm import NotebookLMClient

    title = storyboard_json.get('title', 'Course Content')

    async with await NotebookLMClient.from_storage() as client:
        nb = await client.notebooks.create(f"Course: {title}")
        notebook_id = nb.id

        try:
            # Add course content as pasted text — wait=True ensures the source is
            # fully indexed before we ask NotebookLM to generate anything from it
            truncated = source_text[:50000]
            await client.sources.add_text(nb.id, title, truncated, wait=True, wait_timeout=180.0)

            # Generate audio podcast
            instructions = (
                f"Create an engaging educational podcast about '{title}'. "
                "Make it conversational and suitable for learning. "
                "Cover the key concepts thoroughly."
            )
            status = await client.artifacts.generate_audio(nb.id, instructions=instructions)
            # Allow up to 15 minutes — audio generation is slow
            await client.artifacts.wait_for_completion(nb.id, status.task_id, timeout=900.0)

            # Download the generated audio
            await client.artifacts.download_audio(nb.id, output_path)

            return notebook_id

        except Exception:
            # Attempt cleanup on failure
            try:
                await client.notebooks.delete(nb.id)
            except Exception:
                pass
            raise


async def _generate_infographic_async(source_text, storyboard_json, output_path):
    """Create a NotebookLM notebook, add source text, generate infographic, download PNG."""
    from notebooklm import NotebookLMClient

    title = storyboard_json.get('title', 'Course Content')

    async with await NotebookLMClient.from_storage() as client:
        nb = await client.notebooks.create(f"Course: {title}")
        notebook_id = nb.id

        try:
            truncated = source_text[:50000]
            await client.sources.add_text(nb.id, title, truncated, wait=True, wait_timeout=180.0)

            # Generate infographic
            status = await client.artifacts.generate_infographic(nb.id)
            # Allow up to 15 minutes — infographic generation can be slow
            await client.artifacts.wait_for_completion(nb.id, status.task_id, timeout=900.0)

            # Download the generated infographic
            await client.artifacts.download_infographic(nb.id, output_path)

            return notebook_id

        except Exception:
            try:
                await client.notebooks.delete(nb.id)
            except Exception:
                pass
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

def generate_podcast(source_text, storyboard_json, output_path):
    """Sync wrapper: generate a podcast MP3 from course content."""
    loop = _get_event_loop()
    return loop.run_until_complete(
        _generate_podcast_async(source_text, storyboard_json, output_path)
    )


def generate_infographic(source_text, storyboard_json, output_path):
    """Sync wrapper: generate an infographic PNG from course content."""
    loop = _get_event_loop()
    return loop.run_until_complete(
        _generate_infographic_async(source_text, storyboard_json, output_path)
    )


def cleanup_notebook(notebook_id):
    """Sync wrapper: delete a temporary NotebookLM notebook."""
    loop = _get_event_loop()
    loop.run_until_complete(_cleanup_notebook_async(notebook_id))


def check_auth():
    """Sync wrapper: test whether NotebookLM auth is valid. Returns True or raises."""
    loop = _get_event_loop()
    return loop.run_until_complete(_check_auth_async())
