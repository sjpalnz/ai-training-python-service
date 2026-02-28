"""
Embedding utilities for RAG (Retrieval Augmented Generation).

Uses fastembed with BAAI/bge-small-en-v1.5 (384-dimensional vectors).
fastembed uses ONNX Runtime instead of PyTorch — the package is ~150 MB
total vs ~8 GB for sentence-transformers+torch, making it Railway-friendly.

The model is loaded once as a singleton on first use.
"""

from fastembed import TextEmbedding

_model = None


def get_model() -> TextEmbedding:
    """Return the shared embedding model, loading it on first call."""
    global _model
    if _model is None:
        print("[embeddings] Loading BAAI/bge-small-en-v1.5 via fastembed …")
        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        print("[embeddings] Model ready.")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings and return a list of float vectors."""
    model = get_model()
    # fastembed returns a generator of numpy arrays
    return [emb.tolist() for emb in model.embed(texts)]


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """
    Split text into overlapping fixed-size character chunks.

    Args:
        text:       Input text string.
        chunk_size: Target length of each chunk in characters (~200 tokens).
        overlap:    Characters repeated between consecutive chunks so passages
                    at boundaries are not lost.

    Returns:
        List of non-empty stripped chunk strings.
    """
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks
