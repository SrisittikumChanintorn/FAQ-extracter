"""
clustering.py — Stages 6 & 7: Semantic Clustering + Quality Filtering
Uses HDBSCAN on L2-normalised embeddings (euclidean ≡ cosine on unit vectors).
Filters out small/noisy clusters that don't represent meaningful FAQ topics.
"""

import logging
import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    CLUSTER_MAX_INTRA_DISTANCE,
    CLUSTER_METRIC,
    CLUSTER_MIN_CLUSTER_SIZE,
    CLUSTER_MIN_SAMPLES,
    CLUSTER_MIN_SIZE_THRESHOLD,
    CLUSTER_SELECTION_METHOD,
    FIELD_CLEAN_QUESTION,
    FIELD_CLUSTER_ID,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)
from backend.embedding_service import l2_normalize

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def _compute_intra_cluster_distance(cluster_embs: np.ndarray) -> float:
    """
    Compute average pairwise Euclidean distance within a cluster.
    Lower = more coherent cluster.
    """
    n = len(cluster_embs)
    if n <= 1:
        return 0.0
    # Pairwise squared distances via broadcast
    diff = cluster_embs[:, np.newaxis, :] - cluster_embs[np.newaxis, :, :]
    sq_dist = (diff ** 2).sum(axis=2)
    # Upper triangular → average
    upper = sq_dist[np.triu_indices(n, k=1)]
    return float(np.sqrt(upper).mean())


def run_clustering(
    df: pd.DataFrame,
    embeddings: np.ndarray,
    min_cluster_size: int = CLUSTER_MIN_CLUSTER_SIZE,
    min_samples: int = CLUSTER_MIN_SAMPLES,
    metric: str = CLUSTER_METRIC,
    selection_method: str = CLUSTER_SELECTION_METHOD,
) -> pd.DataFrame:
    """
    Stage 6: Run HDBSCAN clustering.

    Args:
        df: DataFrame with at least FIELD_CLEAN_QUESTION.
        embeddings: Raw float32 embeddings (N, D).
        min_cluster_size: HDBSCAN minimum cluster size.
        min_samples: HDBSCAN min_samples param.
        metric: Distance metric.
        selection_method: HDBSCAN cluster selection method.

    Returns:
        df with FIELD_CLUSTER_ID column added (-1 = noise).
    """
    try:
        import hdbscan
    except ImportError:
        raise ImportError("hdbscan is required: pip install hdbscan")

    norm_embs = l2_normalize(embeddings)

    logger.info(
        f"Stage 6: Running HDBSCAN on {len(norm_embs)} points "
        f"(min_cluster_size={min_cluster_size}, min_samples={min_samples}) …"
    )

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric=metric,
        cluster_selection_method=selection_method,
        core_dist_n_jobs=-1,  # use all CPU cores
    )
    labels = clusterer.fit_predict(norm_embs)

    df = df.copy()
    df[FIELD_CLUSTER_ID] = labels

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = (labels == -1).sum()
    logger.info(
        f"Stage 6 complete: {n_clusters} clusters found, {n_noise} noise points "
        f"({n_noise / len(labels) * 100:.1f}% noise)."
    )
    return df


def filter_clusters(
    df: pd.DataFrame,
    embeddings: np.ndarray,
    min_size: int = CLUSTER_MIN_SIZE_THRESHOLD,
    max_intra_distance: float = CLUSTER_MAX_INTRA_DISTANCE,
) -> pd.DataFrame:
    """
    Stage 7: Quality-filter clusters.

    Rules:
      1. Clusters with fewer than min_size members → discarded (set to noise=-1).
      2. Clusters whose average intra-cluster Euclidean distance > max_intra_distance
         → considered too diverse → discarded.

    Args:
        df: DataFrame with FIELD_CLUSTER_ID.
        embeddings: Corresponding embeddings (same order as df).
        min_size: Minimum cluster size to keep.
        max_intra_distance: Maximum average pairwise distance to keep.

    Returns:
        df with updated FIELD_CLUSTER_ID (discarded → -1).
    """
    norm_embs = l2_normalize(embeddings)
    df = df.copy()

    unique_clusters = [c for c in df[FIELD_CLUSTER_ID].unique() if c != -1]
    discarded = []

    for cluster_id in unique_clusters:
        mask = df[FIELD_CLUSTER_ID] == cluster_id
        cluster_embs = norm_embs[mask.values]

        # Rule 1: size check
        if cluster_embs.shape[0] < min_size:
            df.loc[mask, FIELD_CLUSTER_ID] = -1
            discarded.append((cluster_id, "too_small", cluster_embs.shape[0]))
            continue

        # Rule 2: diversity check
        avg_dist = _compute_intra_cluster_distance(cluster_embs)
        if avg_dist > max_intra_distance:
            df.loc[mask, FIELD_CLUSTER_ID] = -1
            discarded.append((cluster_id, "too_diverse", avg_dist))

    valid_clusters = len([c for c in df[FIELD_CLUSTER_ID].unique() if c != -1])
    logger.info(
        f"Stage 7 complete: {valid_clusters} quality clusters kept, "
        f"{len(discarded)} discarded. "
        f"Discards: {discarded[:10]}{'...' if len(discarded) > 10 else ''}"
    )
    return df
