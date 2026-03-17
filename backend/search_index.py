"""
search_index.py — Stage 11: FAISS Vector Search Index

Builds and queries a FAISS IndexFlatIP over group question embeddings.
Inner product on L2-normalised 1024-dim vectors == cosine similarity.
The index metadata now stores the full group schema (group_id, group_name,
representative_questions, suggested_admin_reply).
"""

import json
import logging
import os
import sys
from typing import Any

import faiss
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    EMBEDDING_DIMENSION,
    FAISS_INDEX_FILE,
    FAISS_META_FILE,
    FAISS_TOP_K_DEFAULT,
    FAISS_USE_GPU,
    SEARCH_MAX_TOP_K,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)
from backend.embedding_service import encode_texts, l2_normalize

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


class FAQSearchIndex:
    """
    FAISS-backed semantic search index for FAQ groups.

    The index is built from the representative questions of each group,
    with each vector pointing back to its parent group record.
    Supports save/load to disk for persistence across API restarts.
    """

    def __init__(self) -> None:
        self._index: faiss.Index | None = None
        self._groups: list[dict[str, Any]] = []
        self._vector_to_group: list[int] = []  # maps FAISS vector idx → group list idx

    def build(self, groups: list[dict[str, Any]]) -> None:
        """
        Stage 11: Build FAISS index from group list.

        Each group contributes its representative_questions as index vectors.
        This gives richer retrieval surface compared to indexing only one question.

        Args:
            groups: List of group dicts with 'representative_questions' and
                    all other expected group fields.
        """
        if not groups:
            raise ValueError("Cannot build index from empty groups list.")

        self._groups = groups
        self._vector_to_group = []
        all_texts: list[str] = []

        for g_idx, group in enumerate(groups):
            rep_qs = group.get("representative_questions", [])
            # Fallback: if no representative questions, use group_name
            if not rep_qs:
                rep_qs = [group.get("group_name") or "Other"]
            for q in rep_qs:
                all_texts.append(q)
                self._vector_to_group.append(g_idx)

        logger.info(
            f"Stage 11: Encoding {len(all_texts)} representative questions "
            f"from {len(groups)} groups for FAISS index …"
        )

        # Queries during search will use is_query=True; passages are encoded here
        raw_embs = encode_texts(all_texts, is_query=False)
        norm_embs = l2_normalize(raw_embs)  # cosine via inner product

        # IndexFlatIP = exact inner product (cosine on normalised vecs)
        index = faiss.IndexFlatIP(EMBEDDING_DIMENSION)
        if FAISS_USE_GPU:
            res = faiss.StandardGpuResources()
            index = faiss.index_cpu_to_gpu(res, 0, index)

        index.add(norm_embs)
        self._index = index
        logger.info(
            f"Stage 11 complete: FAISS index built with {index.ntotal} vectors "
            f"covering {len(groups)} groups."
        )

    def search(
        self,
        query: str,
        top_k: int = FAISS_TOP_K_DEFAULT,
    ) -> list[dict[str, Any]]:
        """
        Search for top-k FAQ groups similar to the query.

        Args:
            query:  Natural language query string (Thai/English).
            top_k:  Maximum number of unique groups to return.

        Returns:
            List of group dicts with added 'similarity_score' field (0–1).
            De-duplicated: each group appears at most once (best score wins).
        """
        if self._index is None:
            raise RuntimeError("Index not built. Call build() first.")

        # Fetch more candidates to allow dedup across representative questions
        fetch_k = min(top_k * 5, SEARCH_MAX_TOP_K * 5, self._index.ntotal)

        # Use query prefix for asymmetric retrieval
        raw_emb = encode_texts([query], is_query=True)
        norm_emb = l2_normalize(raw_emb)

        distances, indices = self._index.search(norm_emb, fetch_k)

        # De-duplicate: keep best score per group
        seen_groups: dict[int, float] = {}
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            g_idx = self._vector_to_group[idx]
            score = float(dist)
            if g_idx not in seen_groups or score > seen_groups[g_idx]:
                seen_groups[g_idx] = score
            if len(seen_groups) >= top_k:
                break

        results = []
        for g_idx, score in sorted(seen_groups.items(), key=lambda x: -x[1]):
            result = dict(self._groups[g_idx])
            result["similarity_score"] = round(score, 4)
            results.append(result)

        return results

    def search_similar_questions(
        self,
        query: str,
        questions: list[str],
        question_embeddings: np.ndarray,
        top_k: int = FAISS_TOP_K_DEFAULT,
    ) -> list[dict[str, Any]]:
        """
        Search for similar historical questions (not groups).
        Used by the /similar_questions endpoint.
        """
        top_k = min(top_k, SEARCH_MAX_TOP_K, len(questions))
        raw_emb = encode_texts([query], is_query=True)
        norm_emb = l2_normalize(raw_emb)

        temp_index = faiss.IndexFlatIP(EMBEDDING_DIMENSION)
        norm_q_embs = l2_normalize(question_embeddings)
        temp_index.add(norm_q_embs)

        distances, indices = temp_index.search(norm_emb, top_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            results.append(
                {
                    "question": questions[idx],
                    "similarity_score": float(round(float(dist), 4)),
                }
            )
        return results

    def save(
        self,
        index_path: str = FAISS_INDEX_FILE,
        meta_path: str = FAISS_META_FILE,
    ) -> None:
        """Persist FAISS index and group metadata to disk."""
        if self._index is None:
            raise RuntimeError("No index to save.")
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        faiss.write_index(self._index, index_path)
        meta = {
            "groups": self._groups,
            "vector_to_group": self._vector_to_group,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.info(f"FAISS index saved to {index_path}, metadata to {meta_path}")

    def load(
        self,
        index_path: str = FAISS_INDEX_FILE,
        meta_path: str = FAISS_META_FILE,
    ) -> bool:
        """Load FAISS index and group metadata from disk. Returns True on success."""
        if not os.path.isfile(index_path) or not os.path.isfile(meta_path):
            logger.warning("FAISS index files not found, skipping load.")
            return False
        self._index = faiss.read_index(index_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        # Support both old format (list of faqs) and new format (dict with groups)
        if isinstance(meta, list):
            self._groups = meta
            self._vector_to_group = list(range(len(meta)))
        else:
            self._groups = meta.get("groups", [])
            self._vector_to_group = meta.get("vector_to_group", list(range(len(self._groups))))
        logger.info(
            f"Loaded FAISS index ({self._index.ntotal} vectors) "
            f"covering {len(self._groups)} groups."
        )
        return True

    @property
    def faqs(self) -> list[dict[str, Any]]:
        """Backwards compat alias."""
        return self._groups

    @property
    def groups(self) -> list[dict[str, Any]]:
        return self._groups

    @property
    def is_ready(self) -> bool:
        return self._index is not None and len(self._groups) > 0
