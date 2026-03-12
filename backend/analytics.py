"""
analytics.py — Stage 13: Analytics Report Generator
Produces structured insights about FAQ clusters, noise, and top topics.
"""

import logging
import sys
import os
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    FIELD_ANSWER,
    FIELD_CLEAN_QUESTION,
    FIELD_CLUSTER_ID,
    FIELD_IS_DUPLICATE,
    FIELD_QUESTION,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def generate_analytics(
    raw_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    unique_df: pd.DataFrame,
    clustered_df: pd.DataFrame,
    faqs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Stage 13: Generate analytics report.

    Args:
        raw_df: Original loaded dataset (before cleaning/filtering).
        valid_df: After question filtering.
        unique_df: After deduplication.
        clustered_df: After clustering (has cluster_id).
        faqs: Final FAQ list.

    Returns:
        Structured analytics dict.
    """
    logger.info("Stage 13: Generating analytics report …")

    total_conversations = len(raw_df)
    total_valid = len(valid_df)
    total_unique = len(unique_df)

    # Noise / unclustered
    noise_mask = clustered_df[FIELD_CLUSTER_ID] == -1
    n_noise = noise_mask.sum()
    n_clustered = (~noise_mask).sum()
    noise_ratio = round(n_noise / total_unique * 100, 2) if total_unique > 0 else 0.0

    # Deduplication stats
    n_duplicates = 0
    if FIELD_IS_DUPLICATE in valid_df.columns:
        n_duplicates = valid_df[FIELD_IS_DUPLICATE].sum()

    # Cluster sizes
    cluster_sizes = []
    for cluster_id in sorted(set(clustered_df[FIELD_CLUSTER_ID].unique()) - {-1}):
        size = int((clustered_df[FIELD_CLUSTER_ID] == cluster_id).sum())
        # Find corresponding FAQ
        faq = next((f for f in faqs if f["cluster_id"] == cluster_id), None)
        cluster_sizes.append(
            {
                "cluster_id": int(cluster_id),
                "size": size,
                "faq_question": faq["faq_question"] if faq else "",
                "support_count": faq["support_count"] if faq else size,
            }
        )
    cluster_sizes.sort(key=lambda x: x["support_count"], reverse=True)

    # Top FAQ topics (top 10)
    top_faqs = faqs[:10]

    # Unanswered / noise questions (sample of noise points)
    noise_questions = (
        clustered_df[noise_mask][FIELD_CLEAN_QUESTION]
        .dropna()
        .head(20)
        .tolist()
    )

    # Average cluster size
    avg_cluster_size = (
        round(sum(c["size"] for c in cluster_sizes) / len(cluster_sizes), 2)
        if cluster_sizes
        else 0
    )

    report = {
        "summary": {
            "total_conversations": int(total_conversations),
            "total_valid_questions": int(total_valid),
            "total_after_deduplication": int(total_unique),
            "total_duplicates_removed": int(n_duplicates),
            "total_clusters": len(cluster_sizes),
            "total_clustered_questions": int(n_clustered),
            "total_noise_questions": int(n_noise),
            "noise_ratio_percent": noise_ratio,
            "total_faqs_generated": len(faqs),
            "average_cluster_size": avg_cluster_size,
        },
        "top_faq_topics": [
            {
                "rank": i + 1,
                "faq_question": f["faq_question"],
                "support_count": f["support_count"],
                "cluster_id": f["cluster_id"],
            }
            for i, f in enumerate(top_faqs)
        ],
        "cluster_sizes": cluster_sizes,
        "unanswered_noise_questions": noise_questions,
    }

    logger.info(
        f"Stage 13 complete: Report generated. "
        f"{len(faqs)} FAQs, {len(cluster_sizes)} clusters, "
        f"{noise_ratio}% noise."
    )
    return report
