"""
batch_merger.py — Merge batch results: combine groups by name, dedupe FAQs by question similarity, sum mention_count.
"""

import logging
import os
import sys
import re
import unicodedata
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    MERGE_GROUP_NAME_MIN_SIMILARITY,
    MERGE_GROUP_USE_EMBEDDING,
    MERGE_QUESTION_SIMILARITY_THRESHOLD,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)

_RE_WS = re.compile(r"\s+")
_RE_EDGE_PUNCT = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)


def _normalize_question_key(text: str) -> str:
    """
    Normalize a question into a stable key for exact deduping.
    Handles differences like:
    - case (sensitive vs insensitive)
    - extra/mixed whitespace
    - leading/trailing punctuation
    - unicode width/compatibility forms
    """
    s = unicodedata.normalize("NFKC", (text or ""))
    s = _RE_WS.sub(" ", s).strip()
    s = _RE_EDGE_PUNCT.sub("", s).strip()
    return s.casefold()


def _merge_into_existing_exact(merged: list[dict], incoming: dict) -> bool:
    """Exact-merge incoming FAQ into merged list using normalized question key."""
    q_in = incoming.get("question", "")
    k_in = _normalize_question_key(q_in)
    if not k_in:
        return False
    for m in merged:
        if _normalize_question_key(m.get("question", "")) == k_in:
            cnt = incoming.get("mention_count", 1)
            before_cnt = m.get("mention_count", 1)
            # Pick answer using pre-merge counts (otherwise the updated total will bias toward current).
            m["answer"] = _pick_better_answer({"answer": m.get("answer", ""), "mention_count": before_cnt}, incoming)
            m["mention_count"] = before_cnt + cnt
            return True
    return False


def _normalize_name(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("\u3000", "").casefold()


def _group_name_similarity(a: str, b: str) -> float:
    """Simple similarity: normalized strings equal or one contains the other."""
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return max(len(na) / max(len(nb), 1), len(nb) / max(len(na), 1))
    # Jaccard on character level
    sa, sb = set(na), set(nb)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _pick_better_answer(current: dict, incoming: dict) -> str:
    """Choose the better answer: prefer higher mention_count, then longer (more complete) text."""
    cnt_c = current.get("mention_count", 1)
    cnt_i = incoming.get("mention_count", 1)
    ans_c = (current.get("answer") or "").strip()
    ans_i = (incoming.get("answer") or "").strip()
    if cnt_i > cnt_c:
        return ans_i
    if cnt_i < cnt_c:
        return ans_c
    return ans_i if len(ans_i) > len(ans_c) else ans_c


def _merge_faqs_by_similarity(
    faqs_a: list[dict],
    faqs_b: list[dict],
    threshold: float = MERGE_QUESTION_SIMILARITY_THRESHOLD,
) -> list[dict]:
    """
    Merge two lists of faqs. When two questions are similar (embedding cosine >= threshold),
    keep one, sum mention_count, and keep the better answer (higher mention_count then longer).
    Falls back to exact string match if embedding fails.
    """
    if not faqs_b:
        return [dict(f) for f in faqs_a]
    if not faqs_a:
        return [dict(f) for f in faqs_b]

    n_a, n_b = len(faqs_a), len(faqs_b)
    questions_a = [f.get("question", "") for f in faqs_a]
    questions_b = [f.get("question", "") for f in faqs_b]
    all_questions = questions_a + questions_b

    try:
        from backend.embedding_service import encode_texts, l2_normalize
        embs = encode_texts(all_questions, is_query=False, show_progress=False)
        embs = l2_normalize(embs)
        merged = [dict(f) for f in faqs_a]
        for d in merged:
            d.setdefault("mention_count", 1)
        merged_emb_idx = list(range(n_a))
        for j in range(n_b):
            emb_b = embs[n_a + j]
            best_sim = threshold
            best_i = -1
            for i in range(len(merged)):
                sim = float(emb_b @ embs[merged_emb_idx[i]])
                if sim > best_sim:
                    best_sim = sim
                    best_i = i
            cnt = faqs_b[j].get("mention_count", 1)
            if best_i >= 0:
                merged[best_i]["mention_count"] = merged[best_i].get("mention_count", 1) + cnt
                merged[best_i]["answer"] = _pick_better_answer(merged[best_i], faqs_b[j])
            else:
                # If embedding similarity doesn't find a match, still dedupe exact
                if _merge_into_existing_exact(merged, faqs_b[j]):
                    continue
                merged.append({
                    "question": faqs_b[j].get("question", ""),
                    "answer": faqs_b[j].get("answer", ""),
                    "mention_count": cnt,
                })
                merged_emb_idx.append(n_a + j)
        return merged
    except Exception as e:
        logger.warning(f"Embedding merge failed, using exact match: {e}")
    merged = [dict(f) for f in faqs_a]
    for d in merged:
        d.setdefault("mention_count", 1)
    for fb in faqs_b:
        qb = fb.get("question") or ""
        kb = _normalize_question_key(qb)
        cnt = fb.get("mention_count", 1)
        found = False
        for m in merged:
            if _normalize_question_key(m.get("question", "")) == kb and kb:
                before_cnt = m.get("mention_count", 1)
                m["answer"] = _pick_better_answer({"answer": m.get("answer", ""), "mention_count": before_cnt}, fb)
                m["mention_count"] = before_cnt + cnt
                found = True
                break
        if not found:
            merged.append({"question": fb.get("question", ""), "answer": fb.get("answer", ""), "mention_count": cnt})
    return merged


def _group_name_similarity_embedding(name_b: str, out_embs: "np.ndarray") -> tuple[int, float]:
    """Return (best_index, best_cosine). out_embs is (N, dim) L2-normalized."""
    import numpy as np
    try:
        from backend.embedding_service import encode_texts, l2_normalize
        emb_b = l2_normalize(encode_texts([name_b], is_query=False, show_progress=False))
        sims = np.dot(emb_b, out_embs.T).flatten()
        best_i = int(np.argmax(sims))
        return best_i, float(sims[best_i])
    except Exception as e:
        logger.debug(f"Group embedding similarity failed: {e}")
        return -1, 0.0


def merge_two_batch_results(
    result_a: list[dict[str, Any]],
    result_b: list[dict[str, Any]],
    name_sim_min: float = MERGE_GROUP_NAME_MIN_SIMILARITY,
) -> list[dict[str, Any]]:
    """
    Merge two batch results into one. Groups are matched by name similarity
    (embedding if MERGE_GROUP_USE_EMBEDDING else string); FAQs merged by question similarity.
    """
    if not result_b:
        return [dict(g) for g in result_a]
    if not result_a:
        return [dict(g) for g in result_b]

    out: list[dict] = []
    for ga in result_a:
        out.append({"group_name": ga.get("group_name", "Other"), "faqs": [dict(f) for f in ga.get("faqs", [])]})

    out_names = [go.get("group_name", "") or "Other" for go in out]
    out_embs = None
    if MERGE_GROUP_USE_EMBEDDING and out_names:
        try:
            from backend.embedding_service import encode_texts, l2_normalize
            import numpy as np
            out_embs = l2_normalize(encode_texts(out_names, is_query=False, show_progress=False))
        except Exception as e:
            logger.debug(f"Precompute group embeddings failed: {e}")

    for gb in result_b:
        name_b = (gb.get("group_name") or "").strip() or "Other"
        faqs_b = gb.get("faqs", [])
        if not faqs_b:
            continue
        best_idx = -1
        best_sim = name_sim_min
        if out_embs is not None:
            idx, sim = _group_name_similarity_embedding(name_b, out_embs)
            if sim >= name_sim_min:
                best_idx = idx
                best_sim = sim
        if best_idx < 0:
            for i, go in enumerate(out):
                sim = _group_name_similarity(go.get("group_name", ""), name_b)
                if sim >= best_sim:
                    best_sim = sim
                    best_idx = i
        if best_idx >= 0:
            merged_faqs = _merge_faqs_by_similarity(out[best_idx]["faqs"], faqs_b)
            out[best_idx]["faqs"] = merged_faqs
        else:
            out.append({"group_name": name_b, "faqs": [dict(f) for f in faqs_b]})
            out_names.append(name_b)
            if out_embs is not None:
                try:
                    from backend.embedding_service import encode_texts, l2_normalize
                    import numpy as np
                    new_emb = l2_normalize(encode_texts([name_b], is_query=False, show_progress=False))
                    out_embs = np.vstack([out_embs, new_emb])
                except Exception:
                    out_embs = None

    return out


def _final_dedup_faqs(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Final pass: within each group, collapse FAQ items whose questions
    differ only in case, whitespace, or leading/trailing punctuation.
    Keeps the entry with the higher mention_count (ties: longer answer wins).
    """
    for g in groups:
        faqs = g.get("faqs", [])
        if len(faqs) <= 1:
            continue
        seen: dict[str, int] = {}  # normalized_key → index in deduped
        deduped: list[dict] = []
        for faq in faqs:
            key = _normalize_question_key(faq.get("question", ""))
            if not key:
                deduped.append(faq)
                continue
            if key in seen:
                existing = deduped[seen[key]]
                cnt = faq.get("mention_count", 1)
                before_cnt = existing.get("mention_count", 1)
                existing["answer"] = _pick_better_answer(
                    {"answer": existing.get("answer", ""), "mention_count": before_cnt},
                    faq,
                )
                existing["mention_count"] = before_cnt + cnt
            else:
                seen[key] = len(deduped)
                deduped.append(dict(faq))
        if len(deduped) < len(faqs):
            logger.info(
                f"  Dedup group '{g.get('group_name', '?')}': {len(faqs)} → {len(deduped)} FAQs"
            )
        g["faqs"] = deduped
    return groups


def merge_all_batch_results(batch_results: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """
    Repeatedly merge pairs (1+2, 3+4, ...) until one final list of groups,
    then run a final dedup pass to collapse case/whitespace-only differences.
    """
    if not batch_results:
        return []
    current = [list(r) for r in batch_results]
    while len(current) > 1:
        next_list = []
        for i in range(0, len(current), 2):
            if i + 1 < len(current):
                merged = merge_two_batch_results(current[i], current[i + 1])
                next_list.append(merged)
            else:
                next_list.append(current[i])
        current = next_list
        logger.info(f"Merge round complete: {len(current)} batch(es) remaining.")
    result = current[0] if current else []
    result = _final_dedup_faqs(result)
    return result
