"""
question_filter.py — Stage 3: Question Detection
Filters out non-question messages: greetings-only, emoji-only, and very short fragments.
"""

import logging
import re
import sys
import os
import unicodedata

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    EMOJI_PATTERN,
    FIELD_ANSWER,
    FIELD_CLEAN_QUESTION,
    FIELD_QUESTION,
    MAX_QUESTION_LENGTH,
    MIN_QUESTION_LENGTH,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)
from backend.faq_quality import has_question_intent, is_ack_only_answer, is_noise_message

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)

# Pre-compiled patterns
_RE_EMOJI = re.compile(EMOJI_PATTERN, re.UNICODE)
_RE_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)  # punctuation/symbols only
_RE_SINGLE_SHORT_WORD = re.compile(r"^\w{1,4}[.!?]?$")  # e.g. "ok", "yes", "no"

# Greeting-only patterns (after cleaning, if ONLY a greeting remains it's not a question)
_GREETING_ONLY_PATTERNS = re.compile(
    r"^(hi|hello|hey|ok|okay|yes|no|thanks|thank you|bye|goodbye|good|nice|great|sure|alright)\.?$",
    re.IGNORECASE,
)
# Thai-only: polite particles or greeting alone (not a real question)
_GREETING_ONLY_THAI = re.compile(
    r"^(ครับ|ค่ะ|คะ|นะครับ|นะค่ะ|นะคะ|ครับครับ|ค่ะค่ะ|สวัสดี|ขอโทษ|ขอบคุณ|ครับค่ะ)?\.?$",
    re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    """Remove emoji characters from text."""
    return _RE_EMOJI.sub("", text).strip()


def _is_valid_question(clean_question: str) -> tuple[bool, str]:
    """
    Determine if a cleaned question is a real, answerable question.

    Returns:
        (is_valid: bool, rejection_reason: str)
    """
    if not clean_question or not isinstance(clean_question, str):
        return False, "empty_after_cleaning"

    # Remove emojis for length check
    text_no_emoji = _strip_emoji(clean_question)
    text_stripped = text_no_emoji.strip()

    # 1. Too short
    if len(text_stripped) < MIN_QUESTION_LENGTH:
        return False, f"too_short ({len(text_stripped)} chars)"

    # 2. Too long (noise / spam)
    if len(text_stripped) > MAX_QUESTION_LENGTH:
        return False, f"too_long ({len(text_stripped)} chars)"

    # 3. Emoji-only message (after stripping emojis, nothing substantial remains)
    if len(text_stripped) == 0:
        return False, "emoji_only"

    # 4. Punctuation / symbols only
    if _RE_PUNCT_ONLY.match(text_stripped):
        return False, "punctuation_only"

    # 5. Greeting-only (English)
    if _GREETING_ONLY_PATTERNS.match(text_stripped):
        return False, "greeting_only"

    # 5b. Thai greeting/politeness only (no real question)
    if _GREETING_ONLY_THAI.match(text_stripped) and len(text_stripped) <= 20:
        return False, "greeting_only_thai"

    # 6. Contains at least one Thai or Latin alphabet character
    has_alpha = any(unicodedata.category(c).startswith("L") for c in text_stripped)
    if not has_alpha:
        return False, "no_alphabetic_content"

    # 7. Short lines without clear question intent are usually chit-chat
    if len(text_stripped) < 18 and not has_question_intent(text_stripped):
        return False, "no_question_intent_short"

    return True, ""


def _is_valid_answer_for_row(answer: str) -> tuple[bool, str]:
    """Drop rows where the agent message cannot support a real FAQ answer."""
    ans = str(answer or "").strip()
    if not ans:
        return False, "empty_answer"
    if is_noise_message(ans):
        return False, "noise_answer"
    if is_ack_only_answer(ans):
        return False, "ack_only_answer"
    if len(ans) < 8:
        return False, "answer_too_short"
    return True, ""


def filter_questions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Stage 3 entry point.
    Applies validity checks to each row and returns only valid questions.

    Input columns required: question, clean_question
    Output: filtered DataFrame (valid_questions) + rejection report logged.

    Returns:
        pd.DataFrame — valid questions only
    """
    logger.info("Stage 3: Filtering valid questions …")

    df = df.copy()

    valid_flags = []
    rejection_reasons = []

    for _, row in df.iterrows():
        clean_q = row.get(FIELD_CLEAN_QUESTION, row.get(FIELD_QUESTION, ""))
        valid, reason = _is_valid_question(str(clean_q))
        if valid and FIELD_ANSWER in row:
            ok_a, ra = _is_valid_answer_for_row(row.get(FIELD_ANSWER, ""))
            if not ok_a:
                valid, reason = False, ra
        valid_flags.append(valid)
        rejection_reasons.append(reason)

    df["_is_valid"] = valid_flags
    df["_rejection_reason"] = rejection_reasons

    rejected = df[~df["_is_valid"]].copy()
    valid_df = df[df["_is_valid"]].copy()

    # Log rejection summary
    if len(rejected) > 0:
        reason_counts = rejected["_rejection_reason"].value_counts().to_dict()
        logger.info(f"Rejected {len(rejected)} rows. Breakdown: {reason_counts}")

    # Drop helper columns
    valid_df = valid_df.drop(columns=["_is_valid", "_rejection_reason"]).reset_index(drop=True)

    logger.info(f"Stage 3 complete: {len(valid_df)} valid questions retained.")
    return valid_df
