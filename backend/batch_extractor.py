"""
batch_extractor.py — LLM-centric FAQ extraction with grouping per batch

Splits data into n_splits; for each split, runs LLM on micro-batches to extract
FAQs and assign each to a category (group_name). Returns one "batch result" per split:
list of { group_name, faqs: [{ question, answer, mention_count }] }.
"""

import json
import logging
import os
import re
import sys
import unicodedata
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    FAQ_EXTRACTION_BATCH_SIZE,
    FAQ_EXTRACTION_NUM_PREDICT,
    FAQ_N_SPLITS,
    FAQ_PER_BATCH,
    FIELD_ANSWER,
    FIELD_QUESTION,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    OLLAMA_RETRY_COUNT,
    OLLAMA_RETRY_DELAY_SEC,
    OLLAMA_TIMEOUT,
    TOPIC_NAMER_MODEL,
    TOPIC_NAMER_OLLAMA_URL,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)

_RE_WS = re.compile(r"\s+")
_RE_EDGE_PUNCT = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)


def _normalize_question_key(text: str) -> str:
    s = unicodedata.normalize("NFKC", (text or ""))
    s = _RE_WS.sub(" ", s).strip()
    s = _RE_EDGE_PUNCT.sub("", s).strip()
    return s.casefold()


def _dedupe_faqs_exact(faqs: list[dict]) -> list[dict]:
    """
    Deduplicate FAQs within a single LLM response group by normalized question key.
    This removes duplicates that differ only by case/whitespace/punctuation.
    """
    if not faqs:
        return []
    out: list[dict] = []
    seen: dict[str, int] = {}
    for f in faqs:
        if not isinstance(f, dict):
            continue
        q = (f.get("question") or "").strip()
        a = (f.get("answer") or "").strip()
        if len(q) < 3 or len(a) < 3 or a == q:
            continue
        k = _normalize_question_key(q)
        if not k:
            continue
        if k in seen:
            idx = seen[k]
            out[idx]["mention_count"] = out[idx].get("mention_count", 1) + int(f.get("mention_count", 1) or 1)
            # Prefer a more informative answer when counts are equal (simple heuristic)
            if len(a) > len((out[idx].get("answer") or "").strip()):
                out[idx]["answer"] = a
            continue
        seen[k] = len(out)
        out.append({"question": q, "answer": a, "mention_count": int(f.get("mention_count", 1) or 1)})
    return out

# Prompt: extract FAQs and assign to a short category (group). One JSON array of groups.
_EXTRACT_AND_GROUP_SYSTEM = """You are an expert at extracting FAQs from customer–agent conversations.

Your task:
1) Extract only real question–answer pairs that appear in the conversations (frequent or useful as FAQs).
2) Assign each pair into an appropriate topic group. The group_name must be short (3–5 words) and in English, e.g. "Login issues", "Account verification".
3) Questions should be neutral and generic (no personal names).
4) Answers must come from what the admin/agent actually replied. If the exchange is not a clear Q&A, skip it.

Return ONLY a JSON array (no extra text):
[{"group_name":"Category name","faqs":[{"question":"...","answer":"..."}]}]"""


def _format_conversations(rows: list[dict]) -> str:
    lines = []
    for i, row in enumerate(rows, 1):
        q = str(row.get(FIELD_QUESTION, row.get("customer_message", ""))).strip()
        a = str(row.get(FIELD_ANSWER, row.get("admin_reply", ""))).strip()
        if q and a:
            lines.append(f"[{i}] Customer: {q}")
            lines.append(f"    Admin: {a}")
    return "\n".join(lines)


def _parse_grouped_faq_json(raw: str) -> list[dict]:
    """Parse LLM response into list of { group_name, faqs: [{ question, answer }] }."""
    if not raw:
        return []
    raw = raw.strip()
    for prefix in ("```json", "```"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :].strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", raw)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("group_name", "")).strip() or "Other"
        faqs = item.get("faqs", item.get("items", []))
        if not isinstance(faqs, list):
            # Single { question, answer } → one group
            q = str(item.get("question", "")).strip()
            a = str(item.get("answer", "")).strip()
            if len(q) >= 3 and len(a) >= 3 and a != q:
                out.append({"group_name": name, "faqs": _dedupe_faqs_exact([{"question": q, "answer": a, "mention_count": 1}])})
            continue
        valid_faqs = []
        for f in faqs:
            if not isinstance(f, dict):
                continue
            q = str(f.get("question", "")).strip()
            a = str(f.get("answer", "")).strip()
            if len(q) >= 3 and len(a) >= 3 and a != q:
                valid_faqs.append({"question": q, "answer": a, "mention_count": 1})
        valid_faqs = _dedupe_faqs_exact(valid_faqs)
        if valid_faqs:
            out.append({"group_name": name, "faqs": valid_faqs})
    # If LLM returned flat list of { question, answer }, wrap in one group
    if not out and data:
        flat = []
        for item in data:
            if isinstance(item, dict):
                q = str(item.get("question", "")).strip()
                a = str(item.get("answer", "")).strip()
                if len(q) >= 3 and len(a) >= 3 and a != q:
                    flat.append({"question": q, "answer": a, "mention_count": 1})
        if flat:
            out = [{"group_name": "Other", "faqs": _dedupe_faqs_exact(flat)}]
    return out


def _call_llm_extract_and_group(conversations: list[dict]) -> list[dict]:
    """Single LLM call: extract FAQs and assign groups. Returns list of { group_name, faqs }. Retries on failure."""
    try:
        import requests
        import time
    except ImportError:
        raise ImportError("pip install requests")

    if not conversations:
        return []

    text = _format_conversations(conversations)
    user = (
        f"From the following conversations, extract up to {FAQ_PER_BATCH} useful FAQ Q&A pairs "
        f"and organize them into short English topic groups.\n\n{text}\n\n"
        "Return ONLY a JSON array of groups: "
        "[{\"group_name\":\"...\",\"faqs\":[{\"question\":\"...\",\"answer\":\"...\"}]}]"
    )
    prompt = f"[INST] <<SYS>>\n{_EXTRACT_AND_GROUP_SYSTEM}\n<</SYS>>\n\n{user} [/INST]"

    payload = {
        "model": TOPIC_NAMER_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": FAQ_EXTRACTION_NUM_PREDICT, "stop": ["[INST]", "<<SYS>>"]},
    }
    kwargs = {"json": payload}
    if OLLAMA_TIMEOUT is not None:
        kwargs["timeout"] = OLLAMA_TIMEOUT

    last_err = None
    for attempt in range(1, OLLAMA_RETRY_COUNT + 1):
        try:
            resp = requests.post(TOPIC_NAMER_OLLAMA_URL, **kwargs)
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            return _parse_grouped_faq_json(raw)
        except Exception as e:
            last_err = e
            logger.warning(f"LLM extract+group attempt {attempt}/{OLLAMA_RETRY_COUNT} failed: {e}")
            if attempt < OLLAMA_RETRY_COUNT and OLLAMA_RETRY_DELAY_SEC > 0:
                time.sleep(OLLAMA_RETRY_DELAY_SEC)
    logger.warning(f"LLM extract+group failed after {OLLAMA_RETRY_COUNT} attempts. Last: {last_err}")
    return []


def split_data(df: pd.DataFrame, n_splits: int) -> list[pd.DataFrame]:
    """Split DataFrame into n_splits parts (by row)."""
    n = len(df)
    if n == 0 or n_splits <= 0:
        return []
    n_splits = min(n_splits, n)
    size = n // n_splits
    remainder = n % n_splits
    start = 0
    out = []
    for i in range(n_splits):
        take = size + (1 if i < remainder else 0)
        out.append(df.iloc[start : start + take].copy())
        start += take
    return out


def run_one_batch(
    batch_df: pd.DataFrame,
    micro_batch_size: int = FAQ_EXTRACTION_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """
    Process one split: run LLM on each micro-batch, aggregate all groups.
    Returns list of { group_name, faqs: [{ question, answer, mention_count }] }
    with groups merged by name within this batch.
    """
    rows = batch_df.to_dict("records")
    all_groups: list[dict] = []  # will merge by group_name

    for i in range(0, len(rows), micro_batch_size):
        chunk = rows[i : i + micro_batch_size]
        if not chunk:
            continue
        parsed = _call_llm_extract_and_group(chunk)
        for item in parsed:
            name = (item.get("group_name") or "").strip() or "Other"
            faqs = item.get("faqs", [])
            if not faqs:
                continue
            # Merge into existing group by name (exact match within batch)
            found = False
            for g in all_groups:
                if (g.get("group_name") or "").strip() == name:
                    g["faqs"].extend(faqs)
                    found = True
                    break
            if not found:
                all_groups.append({"group_name": name, "faqs": list(faqs)})

    return all_groups


def run_all_batches(
    df: pd.DataFrame,
    n_splits: int = FAQ_N_SPLITS,
    micro_batch_size: int = FAQ_EXTRACTION_BATCH_SIZE,
) -> list[list[dict[str, Any]]]:
    """
    Split data into n_splits, run LLM extract+group on each split.
    Returns list of batch results; each element is list of { group_name, faqs }.
    """
    parts = split_data(df, n_splits)
    if not parts:
        return []

    results = []
    for i, part in enumerate(parts):
        logger.info(f"Batch {i + 1}/{len(parts)}: {len(part)} conversations.")
        batch_result = run_one_batch(part, micro_batch_size)
        results.append(batch_result)
        logger.info(f"  → {len(batch_result)} groups, {sum(len(g['faqs']) for g in batch_result)} FAQs.")
    return results
