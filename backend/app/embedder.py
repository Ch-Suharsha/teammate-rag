from __future__ import annotations

import logging
import threading
from typing import List

import numpy as np

from .settings import get_settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_model = None


def get_embedder():
    """Lazily load the sentence-transformers model.

    Loaded on first use so the API can boot fast and the ingest CLI
    shares the same dependency without preloading on import.
    """
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            settings = get_settings()
            log.info("Loading embedding model: %s", settings.embedding_model)
            _model = SentenceTransformer(settings.embedding_model)
    return _model


def embed_texts(texts: List[str], batch_size: int = 64, normalize: bool = True) -> np.ndarray:
    model = get_embedder()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype="float32")


def embed_query(text: str) -> List[float]:
    return embed_texts([text]).tolist()[0]
