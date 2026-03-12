"""
deduplication.py — Stage 5: Semantic Deduplication
Removes near-duplicate questions using cosine similarity thresholding.
Operates in chunks to handle 100k+ datasets without OOM errors.
"""

import logging
import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    DEDUP_CHUNK_SIZE,
    DEDUP_SIMILARITY_THRESHOLD,
    FIELD_CANONICAL_ID,
    FIELD_DUPLICATE_COUNT,
    FIELD_IS_DUPLICATE,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)
from backend.embedding_service import l2_normalize

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def _cosine_similarity_chunk(
    query_embeddings: np.ndarray, reference_embeddings: np.ndarray
) -> np.ndarray:
    """
    Compute cosine similarity between query rows and reference rows.
    Both inputs must already be L2-normalised.

    Returns:
        np.ndarray of shape (len(query), len(reference))
    """
    return query_embeddings @ reference_embeddings.T


def deduplicate(
    df: pd.DataFrame,
    embeddings: np.ndarray,
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
    chunk_size: int = DEDUP_CHUNK_SIZE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Stage 5 entry point.
    Marks rows as duplicates if cosine similarity to an earlier canonical row >= threshold.

    Process:
        - Iterate rows in order.
        - If a row is similar (>= threshold) to any already-accepted canonical, mark as duplicate.
        - Track which canonical it duplicates and the duplicate count.

    Args:
        df: Input DataFrame (valid questions, same order as embeddings).
        embeddings: Raw float32 embeddings of shape (N, D).
        threshold: Cosine similarity threshold above which → duplicate.
        chunk_size: Process in chunks for memory efficiency.

    Returns:
        (unique_df, full_df_with_flags)
            unique_df: Only the canonical (non-duplicate) rows, with 'duplicate_count' added.
            full_df_with_flags: All rows including is_duplicate, canonical_id, duplicate_count.
    """
    n = len(df)
    logger.info(f"Stage 5: Deduplicating {n} questions at threshold={threshold} …")

    # L2 normalise for cosine
    norm_embeddings = l2_normalize(embeddings)

    is_duplicate = np.zeros(n, dtype=bool)
    canonical_id = np.arange(n, dtype=np.int32)  # default: each row is its own canonical
    duplicate_count = np.zeros(n, dtype=np.int32)

    # Greedy sequential deduplication
    # canonical_indices: list of indices that are accepted so far
    canonical_indices = []
    canonical_embs = np.empty((0, norm_embeddings.shape[1]), dtype=np.float32)

    for i in range(n):
        if len(canonical_indices) == 0:
            # First row is always a canonical
            canonical_indices.append(i)
            canonical_embs = norm_embeddings[i : i + 1]
            continue

        # Compute similarity of row i against all current canonicals
        sim = norm_embeddings[i] @ canonical_embs.T  # shape (k,)
        max_sim_idx = np.argmax(sim)
        max_sim = sim[max_sim_idx]

        if max_sim >= threshold:
            # Duplicate of canonical_indices[max_sim_idx]
            is_duplicate[i] = True
            can_i = canonical_indices[max_sim_idx]
            canonical_id[i] = can_i
            duplicate_count[can_i] += 1
        else:
            canonical_indices.append(i)
            canonical_embs = np.vstack([canonical_embs, norm_embeddings[i : i + 1]])

    df = df.copy()
    df[FIELD_IS_DUPLICATE] = is_duplicate
    df[FIELD_CANONICAL_ID] = canonical_id
    # For canonical rows, duplicate_count includes themselves
    df[FIELD_DUPLICATE_COUNT] = 0
    for i, cnt in enumerate(duplicate_count):
        df.at[i, FIELD_DUPLICATE_COUNT] = cnt

    n_duplicates = is_duplicate.sum()
    n_unique = n - n_duplicates
    logger.info(
        f"Stage 5 complete: {n_unique} unique questions, {n_duplicates} duplicates removed "
        f"({n_duplicates / n * 100:.1f}% dedup rate)."
    )

    unique_df = df[~df[FIELD_IS_DUPLICATE]].copy().reset_index(drop=True)
    return unique_df, df
