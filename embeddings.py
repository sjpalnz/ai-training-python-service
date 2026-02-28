"""
Embedding utilities for RAG (Retrieval Augmented Generation).

Uses sentence-transformers with all-MiniLM-L6-v2 (384-dimensional vectors,
~90 MB, fast on CPU, no API key required).

The model is loaded once as a singleton on first use to avoid repeated
cold-start overhead within the same process.
"""

from sentence_transformers import SentenceTransformer

_model = None


def get_model() -> SentenceTransformer:
    """Return the shared embedding model, loading it on first call."""
    global _model
    if _model is None:
        print("[embeddings] Loading all-MiniLM-L6-v2 …")
        _model = SentenceTransformer('all-MiniLM-L6-v2')
        print("[embeddings] Model ready.")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings and return a list of float vectors."""
    return get_model().encode(texts, convert_to_list=True)


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """
    Split text into overlapping fixed-size character chunks.

    Args:
        text:       Input text string.
        chunk_size: Target length of each chunk in characters (~200 tokens).
        overlap:    Number of characters to repeat between consecutive chunks
                    so that passages at chunk boundaries are not lost.

    Returns:
        List of chunk strings (non-empty, stripped).
    """
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks
