"""FastEmbed singleton for BGE-small-en-v1.5 (384-dim, asymmetric)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, List

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
CACHE_DIR = Path(__file__).parent.parent / "data" / "fastembed_cache"

_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(CACHE_DIR))
    return _model


def embed_passages(texts: Iterable[str]) -> List[np.ndarray]:
    m = _get_model()
    return list(m.passage_embed(list(texts)))


def embed_query(text: str) -> np.ndarray:
    m = _get_model()
    return next(iter(m.query_embed([text])))
