"""
faq_generator.py — Stages 8, 9, 10: FAQ Generation
  Stage 8: Canonical question selection (highest mean cosine similarity to cluster).
  Stage 9: Answer extraction (most frequent; fallback to longest for diversity).
  Stage 10: Final FAQ dataset assembly, sorted by support_count.
"""

import logging
import sys
import os
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    ANSWER_MIN_LENGTH,
    FIELD_ANSWER,
    FIELD_CLEAN_QUESTION,
    FIELD_CLUSTER_ID,
    FIELD_DUPLICATE_COUNT,
    FIELD_QUESTION,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)
from backend.embedding_service import l2_normalize

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def _select_canonical_question(
    questions: list[str],
    clean_questions: list[str],
    cluster_embs: np.ndarray,
) -> str:
    """
    Stage 8: Find the question with the highest average cosine similarity
    to all other questions in the cluster.

    Returns the original (display) question for the chosen representative.
    """
    norm_embs = l2_normalize(cluster_embs)
    n = len(norm_embs)

    if n == 1:
        return questions[0]

    # Pairwise cosine similarity matrix
    sim_matrix = norm_embs @ norm_embs.T  # (n, n)

    # Mean similarity excluding self (diagonal)
    np.fill_diagonal(sim_matrix, 0.0)
    mean_sims = sim_matrix.sum(axis=1) / (n - 1)

    best_idx = int(np.argmax(mean_sims))
    return clean_questions[best_idx]  # Return the cleaned question as the FAQ question


def _select_best_answer(answers: list[str]) -> str:
    """
    Stage 9: Select the best admin answer for a cluster.

    Strategy:
    1. Normalise answers (strip whitespace, lower for comparison).
    2. Pick the most frequent answer.
    3. If all unique → return the longest answer (most informative).

    Returns the original-case most representative answer.
    """
    # Filter empty / too-short answers
    valid = [a for a in answers if isinstance(a, str) and len(a.strip()) >= ANSWER_MIN_LENGTH]
    if not valid:
        return answers[0] if answers else ""

    # Normalise for counting
    normalised = [a.strip().lower() for a in valid]
    counts = Counter(normalised)
    most_common_norm, top_count = counts.most_common(1)[0]

    if top_count > 1:
        # Return the original-case version of the most common answer
        for orig in valid:
            if orig.strip().lower() == most_common_norm:
                return orig.strip()

    # All unique → return the longest (most detailed) answer
    return max(valid, key=len).strip()


def generate_faqs(
    df: pd.DataFrame,
    embeddings: np.ndarray,
) -> list[dict[str, Any]]:
    """
    Stages 8–10 entry point.

    Args:
        df: DataFrame with columns: question, clean_question, answer,
            cluster_id, (optionally) duplicate_count.
        embeddings: Raw float32 embeddings matching df rows.

    Returns:
        List of FAQ dicts sorted by support_count descending:
        [{
            "faq_question": str,
            "faq_answer": str,
            "support_count": int,
            "cluster_id": int,
        }]
    """
    faqs: list[dict] = []
    unique_clusters = sorted([c for c in df[FIELD_CLUSTER_ID].unique() if c != -1])

    logger.info(f"Stages 8-10: Generating FAQs for {len(unique_clusters)} clusters …")

    for cluster_id in unique_clusters:
        mask = df[FIELD_CLUSTER_ID] == cluster_id
        cluster_df = df[mask].copy()
        cluster_embs = embeddings[mask.values]

        questions = cluster_df[FIELD_QUESTION].tolist()
        clean_questions = cluster_df[FIELD_CLEAN_QUESTION].tolist()
        answers = cluster_df[FIELD_ANSWER].tolist()

        # Stage 8: canonical question
        faq_question = _select_canonical_question(questions, clean_questions, cluster_embs)

        # Stage 9: best answer
        faq_answer = _select_best_answer(answers)

        # Stage 10: support count = cluster size + all duplicates that fed into it
        base_count = len(cluster_df)
        extra_dupes = 0
        if FIELD_DUPLICATE_COUNT in cluster_df.columns:
            extra_dupes = cluster_df[FIELD_DUPLICATE_COUNT].sum()
        support_count = base_count + int(extra_dupes)

        faqs.append(
            {
                "faq_question": faq_question,
                "faq_answer": faq_answer,
                "support_count": support_count,
                "cluster_id": int(cluster_id),
            }
        )

    # Stage 10: sort by support_count descending (most requested first)
    faqs.sort(key=lambda x: x["support_count"], reverse=True)

    logger.info(f"Stage 10 complete: {len(faqs)} FAQs generated.")
    return faqs
