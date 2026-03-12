"""
search_index.py — Stage 11: FAISS Vector Search Index
Builds and queries a FAISS IndexFlatIP over FAQ questions.
Inner product on L2-normalised vectors == cosine similarity.
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
    FAISS-backed semantic search index for FAQ questions.
    Supports save/load to disk for persistence across API restarts.
    """

    def __init__(self) -> None:
        self._index: faiss.Index | None = None
        self._faqs: list[dict[str, Any]] = []

    def build(self, faqs: list[dict[str, Any]]) -> None:
        """
        Stage 11: Build FAISS index from FAQ list.

        Args:
            faqs: List of FAQ dicts with at least 'faq_question'.
        """
        if not faqs:
            raise ValueError("Cannot build index from empty FAQ list.")

        self._faqs = faqs
        questions = [f["faq_question"] for f in faqs]

        logger.info(f"Stage 11: Encoding {len(questions)} FAQ questions for FAISS index …")
        raw_embs = encode_texts(questions)
        norm_embs = l2_normalize(raw_embs)  # cosine via inner product

        # IndexFlatIP = exact inner product (cosine on normalised vecs)
        index = faiss.IndexFlatIP(EMBEDDING_DIMENSION)
        if FAISS_USE_GPU:
            res = faiss.StandardGpuResources()
            index = faiss.index_cpu_to_gpu(res, 0, index)

        index.add(norm_embs)
        self._index = index
        logger.info(f"Stage 11 complete: FAISS index built with {index.ntotal} vectors.")

    def search(
        self,
        query: str,
        top_k: int = FAISS_TOP_K_DEFAULT,
    ) -> list[dict[str, Any]]:
        """
        Search for top-k FAQs similar to the query.

        Returns:
            List of dicts: original FAQ fields + 'similarity_score' (0–1).
        """
        if self._index is None:
            raise RuntimeError("Index not built. Call build() first.")

        top_k = min(top_k, SEARCH_MAX_TOP_K, len(self._faqs))

        raw_emb = encode_texts([query])
        norm_emb = l2_normalize(raw_emb)

        distances, indices = self._index.search(norm_emb, top_k)
        # distances are inner products (cosine similarity for normalised vecs)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            result = dict(self._faqs[idx])
            result["similarity_score"] = float(round(float(dist), 4))
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
        Search for similar historical questions (not FAQs).

        Args:
            query: Input query.
            questions: List of historical questions.
            question_embeddings: Pre-computed normalised embeddings for questions.
            top_k: Number of results.

        Returns:
            List of {'question': str, 'similarity_score': float}
        """
        top_k = min(top_k, SEARCH_MAX_TOP_K, len(questions))
        raw_emb = encode_texts([query])
        norm_emb = l2_normalize(raw_emb)

        # Build a temporary index for historical questions
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
        """Persist FAISS index and FAQ metadata to disk."""
        if self._index is None:
            raise RuntimeError("No index to save.")
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        faiss.write_index(self._index, index_path)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(self._faqs, f, ensure_ascii=False, indent=2)
        logger.info(f"FAISS index saved to {index_path}, metadata to {meta_path}")

    def load(
        self,
        index_path: str = FAISS_INDEX_FILE,
        meta_path: str = FAISS_META_FILE,
    ) -> bool:
        """Load FAISS index and FAQ metadata from disk. Returns True on success."""
        if not os.path.isfile(index_path) or not os.path.isfile(meta_path):
            logger.warning("FAISS index files not found, skipping load.")
            return False
        self._index = faiss.read_index(index_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            self._faqs = json.load(f)
        logger.info(
            f"Loaded FAISS index ({self._index.ntotal} vectors) and {len(self._faqs)} FAQs."
        )
        return True

    @property
    def faqs(self) -> list[dict[str, Any]]:
        return self._faqs

    @property
    def is_ready(self) -> bool:
        return self._index is not None and len(self._faqs) > 0
