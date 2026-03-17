"""
analytics.py — Analytics report for LLM batch+merge pipeline.
"""

import logging
import os
import sys
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import LOG_DATE_FORMAT, LOG_FORMAT, LOG_LEVEL

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def generate_analytics_simple(raw_df: pd.DataFrame, groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Analytics from final groups (no clustering)."""
    logger.info("Generating analytics …")
    total_conversations = len(raw_df)
    total_groups = len(groups)
    total_faq_items = sum(g.get("total_faqs", len(g.get("faqs", []))) for g in groups)
    total_mention_count = sum(g.get("support_count", 0) for g in groups)

    cluster_sizes = []
    for i, g in enumerate(groups):
        faqs = g.get("faqs", [])
        size = len(faqs)
        support = g.get("support_count", sum(f.get("mention_count", 1) for f in faqs))
        cluster_sizes.append({
            "cluster_id": i,
            "group_name": (g.get("group_name") or "").strip() or "Other",
            "size": size,
            "support_count": support,
        })
    cluster_sizes.sort(key=lambda x: x["support_count"], reverse=True)

    top_faq_topics = []
    for i, g in enumerate(groups[:10]):
        top_faq_topics.append({
            "rank": i + 1,
            "group_name": (g.get("group_name") or "").strip() or "Other",
            "faq_question": g.get("canonical_question", g.get("faq_question", "")),
            "support_count": g.get("support_count", 0),
            "cluster_id": i,
        })

    report = {
        "summary": {
            "total_conversations": int(total_conversations),
            "total_valid_questions": int(total_conversations),
            "total_groups": int(total_groups),
            "total_faq_items": int(total_faq_items),
            "total_faqs_generated": int(total_groups),
            "total_mention_count": int(total_mention_count),
            "total_clustered_questions": int(total_faq_items),
            "questions_passed_into_groups": int(total_faq_items),
            "questions_discarded": 0,
        },
        "top_faq_topics": top_faq_topics,
        "cluster_sizes": cluster_sizes,
        "unanswered_noise_questions": [],
    }
    logger.info(f"Analytics: {total_groups} groups, {total_faq_items} FAQ items.")
    return report
