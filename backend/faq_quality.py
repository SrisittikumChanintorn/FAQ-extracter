"""
faq_quality.py — High-ROI FAQ filtering and output capping.

Filters noise (stickers, ack-only replies, chit-chat) and caps total FAQ items
after merge, ranked by mention_count and an informational-content score.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import (
    FAQ_MAX_OUTPUT_CAP,
    FAQ_MAX_OUTPUT_DEFAULT,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)

# ── Noise patterns (answers / questions from chat platforms) ─────────────────
_RE_STICKER_OR_MEDIA = re.compile(
    r"(you\s+(sent|uploaded)|sent|uploaded)\s+(a\s+)?(sticker|stickers|photo|photos|image|images|picture|pictures|file|gif|video)\b",
    re.I,
)
_RE_META_LINE = re.compile(
    r"^\s*("
    r"\[(sticker|image|photo|file|voice|audio|video)\]|"
    r"(sticker|image|photo|file)\s+omitted|"
    r"ไฟล์แนบ|"
    r"<\s*(sticker|media|photo)\s*>"
    r")\s*$",
    re.I,
)
_RE_ACK_ONLY_EN = re.compile(
    r"^(ok|okay|k\.?|yes|no|thanks?|thank\s*you|ty|sure|got\s*it|done|hi|hello)\.?!*$",
    re.I,
)
# Thai / mixed acknowledgment-only replies (not usable as FAQ answers)
_RE_ACK_ONLY_TH = re.compile(
    r"^(ครับ|ค่ะ|คะ|นะครับ|นะคะ|ครับครับ|ค่ะค่ะ|ได้ครับ|ได้ค่ะ|โอเค|โอเคครับ|โอเคค่ะ|"
    r"รับทราบ|รับทราบครับ|รับทราบค่ะ|จ้า|จ้ะ|อือ|อืม|ใช่ครับ|ใช่ค่ะ|ไม่ครับ|ไม่ค่ะ)\.?!*$",
    re.UNICODE,
)
# Question side: bare politeness / filler (after normalization)
_RE_Q_GREETING_ONLY_TH = re.compile(
    r"^(ครับ|ค่ะ|คะ|นะครับ|นะคะ)\.?!*$",
    re.UNICODE,
)

# Suggested question intent (Thai / English) — short lines without this are often chit-chat
_RE_QUESTION_INTENT = re.compile(
    r"(\?|？|ไหม|มั้ย|มาย|หรือไม่|หรือเปล่า|หรอ|ไง|ยังไง|ทำไง|อย่างไร|เพราะอะไร|"
    r"ทำไม|กี่|เท่าไหร่|ยังไงบ้าง|ได้ไหม|ได้มั้ย|ใช่ไหม|มีไหม|มีมั้ย|"
    r"ขอสอบถาม|สอบถามเรื่อง|ขอถาม|ขอทราบ|อยากทราบ|รบกวนขอ|ต้องการทราบ|"
    r"\b(how|what|when|where|why|which|who|can|could|would|should|is|are|do|does|did|will)\b)",
    re.I | re.UNICODE,
)

_RE_NON_ALNUM = re.compile(r"[^\w\u0e00-\u0e7f]", re.UNICODE)

MIN_Q_LEN = 10
MIN_A_LEN = 12


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip())


def is_noise_message(text: str) -> bool:
    """True if text looks like a system / media placeholder, not natural language."""
    t = _nfkc(text)
    if not t:
        return True
    low = t.lower()
    if _RE_STICKER_OR_MEDIA.search(low):
        return True
    if _RE_META_LINE.match(t):
        return True
    # Common messenger placeholders
    if "sticker" in low and len(t) < 80:
        return True
    if "sent a sticker" in low or "ส่งสติ๊กเกอร์" in t:
        return True
    return False


def is_ack_only_answer(text: str) -> bool:
    t = _nfkc(text)
    if len(t) <= 1:
        return True
    return bool(_RE_ACK_ONLY_EN.match(t) or _RE_ACK_ONLY_TH.match(t))


def has_question_intent(question: str) -> bool:
    q = _nfkc(question)
    if not q:
        return False
    return bool(_RE_QUESTION_INTENT.search(q))


def _informative_score(q: str, a: str) -> float:
    """Higher = more likely business-useful (length + structure heuristics)."""
    qn, an = _nfkc(q), _nfkc(a)
    score = 0.0
    score += min(len(qn), 400) * 0.08
    score += min(len(an), 800) * 0.04
    if has_question_intent(qn):
        score += 12.0
    digits_q = sum(c.isdigit() for c in qn)
    digits_a = sum(c.isdigit() for c in an)
    if digits_q + digits_a >= 2:
        score += 5.0
    # Thai + Latin mix often indicates product/code terms
    has_thai = any("\u0e00" <= c <= "\u0e7f" for c in qn + an)
    has_latin = any("a" <= c.lower() <= "z" for c in qn + an)
    if has_thai and has_latin:
        score += 3.0
    return score


def is_high_value_faq_pair(question: str, answer: str) -> bool:
    """
    Keep only Q–A pairs that are likely useful for a business FAQ knowledge base.
    """
    q = _nfkc(question)
    a = _nfkc(answer)
    if not q or not a:
        return False
    if q == a:
        return False
    if is_noise_message(q) or is_noise_message(a):
        return False
    if _RE_Q_GREETING_ONLY_TH.match(q):
        return False
    if len(q) < MIN_Q_LEN or len(a) < MIN_A_LEN:
        return False
    # Short questions need clear interrogative / intent
    if len(q) < 18 and not has_question_intent(q):
        return False
    if is_ack_only_answer(a):
        return False
    # Answer only punctuation or emoji-like junk
    alnum = _RE_NON_ALNUM.sub("", a)
    if len(alnum) < 8:
        return False
    return True


def pair_rank_score(question: str, answer: str, mention_count: int) -> float:
    m = max(1, int(mention_count or 1))
    return _informative_score(question, answer) + m * 3.5


def filter_merged_groups(groups: list[dict]) -> list[dict]:
    """Drop low-value FAQs inside each merged group."""
    out = []
    dropped = 0
    kept = 0
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        name = (g.get("group_name") or "").strip() or "Other"
        faqs = g.get("faqs") or []
        if not isinstance(faqs, list):
            continue
        kept_faqs = []
        for f in faqs:
            if not isinstance(f, dict):
                continue
            q = str(f.get("question", "")).strip()
            a = str(f.get("answer", "")).strip()
            if is_high_value_faq_pair(q, a):
                kept_faqs.append(dict(f))
                kept += 1
            else:
                dropped += 1
        if kept_faqs:
            out.append({"group_name": name, "faqs": kept_faqs})
    if dropped:
        logger.info(f"FAQ quality filter: removed {dropped} low-value pair(s), kept {kept}.")
    return out


def cap_total_faqs(groups: list[dict], max_faqs: int | None) -> list[dict]:
    """
    Rank all FAQs globally, keep top max_faqs by (mention_count + quality), regroup by group_name.
    """
    limit = int(max_faqs) if max_faqs is not None else FAQ_MAX_OUTPUT_DEFAULT
    if limit <= 0:
        limit = FAQ_MAX_OUTPUT_DEFAULT
    limit = max(1, min(limit, FAQ_MAX_OUTPUT_CAP))

    flat: list[tuple[float, str, dict]] = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        gname = (g.get("group_name") or "").strip() or "Other"
        for f in g.get("faqs") or []:
            if not isinstance(f, dict):
                continue
            q = str(f.get("question", "")).strip()
            a = str(f.get("answer", "")).strip()
            if not is_high_value_faq_pair(q, a):
                continue
            m = int(f.get("mention_count", 1) or 1)
            sc = pair_rank_score(q, a, m)
            flat.append((sc, gname, {"question": q, "answer": a, "mention_count": m}))

    flat.sort(key=lambda x: -x[0])
    if len(flat) > limit:
        logger.info(f"FAQ cap: keeping top {limit} of {len(flat)} scored pair(s).")
    flat = flat[:limit]

    regroup: dict[str, list] = defaultdict(list)
    for _sc, gname, item in flat:
        regroup[gname].append(item)

    return [{"group_name": k, "faqs": v} for k, v in regroup.items()]


def filter_and_cap_groups(groups: list[dict], max_faqs: int | None) -> list[dict]:
    """Filter noise then apply global cap (single entry point from main pipeline)."""
    g2 = filter_merged_groups(groups)
    return cap_total_faqs(g2, max_faqs)
