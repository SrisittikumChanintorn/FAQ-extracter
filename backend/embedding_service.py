"""
embedding_service.py — Sentence embedding (BAAI/bge-m3) for merge dedupe and search index.
"""

import logging
import os
import sys
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DEVICE,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_PASSAGE_PREFIX,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_SHOW_PROGRESS,
    EMBEDDING_USE_INSTRUCTION,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)

# Singleton model reference (loaded once per process)
_model: Optional[SentenceTransformer] = None


def get_model() -> SentenceTransformer:
    """Return the singleton SentenceTransformer model, loading it if needed."""
    global _model
    if _model is None:
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME} on {EMBEDDING_DEVICE}")
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME, device=EMBEDDING_DEVICE)
        logger.info(
            f"Embedding model loaded. Max seq length: {_model.max_seq_length}, "
            f"Output dim: {_model.get_sentence_embedding_dimension()}"
        )
    return _model


def encode_texts(
    texts: list[str],
    is_query: bool = False,
    batch_size: int = EMBEDDING_BATCH_SIZE,
    show_progress: bool = EMBEDDING_SHOW_PROGRESS,
) -> np.ndarray:
    """
    Encode a list of strings into dense embedding vectors using BAAI/bge-m3.

    Args:
        texts:         List of strings to embed.
        is_query:      If True, prepend the query instruction prefix.
                       Use True for search queries, False for document passages.
        batch_size:    Number of texts per batch.
        show_progress: Whether to show tqdm progress bar.

    Returns:
        np.ndarray of shape (N, EMBEDDING_DIMENSION), dtype float32
    """
    model = get_model()

    if EMBEDDING_USE_INSTRUCTION:
        prefix = EMBEDDING_QUERY_PREFIX if is_query else EMBEDDING_PASSAGE_PREFIX
        if prefix:
            texts = [prefix + t for t in texts]

    logger.info(
        f"Encoding {len(texts)} texts (is_query={is_query}) "
        f"in batches of {batch_size} …"
    )

    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=show_progress,
        convert_to_numpy=True,
        normalize_embeddings=False,  # We normalise manually downstream
    )

    if embeddings.ndim != 2 or embeddings.shape[1] != EMBEDDING_DIMENSION:
        raise ValueError(
            f"Unexpected embedding shape: {embeddings.shape}. "
            f"Expected (N, {EMBEDDING_DIMENSION}). "
            f"Check that EMBEDDING_DIMENSION={EMBEDDING_DIMENSION} matches the model."
        )

    logger.info(f"Encoded {len(embeddings)} embeddings. Shape: {embeddings.shape}")
    return embeddings.astype(np.float32)


def l2_normalize(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize embeddings so inner product == cosine similarity."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-10, norms)
    return embeddings / norms
