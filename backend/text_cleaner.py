"""
text_cleaner.py — Stage 2: Question Cleaning
Removes Thai/English greetings, filler phrases, repeated punctuation,
and normalises whitespace to produce clean_question.
"""

import logging
import re
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    FIELD_CLEAN_QUESTION,
    FIELD_QUESTION,
    THAI_FILLER_PHRASES,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)

# ── Pre-compile regex patterns once ───────────────────────────────────────────

# Repeated punctuation: !!!, ???, ..., ~~~
_RE_REPEATED_PUNCT = re.compile(r"([!?.,~\-_])\1{2,}")

# Multiple spaces / tabs / newlines  →  single space
_RE_WHITESPACE = re.compile(r"\s+")

# English filler openers (case-insensitive)
_ENGLISH_FILLERS = [
    r"^i\s+have\s+a\s+question[,.]?\s*",
    r"^quick\s+question[,.]?\s*",
    r"^just\s+wanted\s+to\s+ask[,.]?\s*",
    r"^excuse\s+me[,.]?\s*",
    r"^sorry\s+to\s+bother\s+you[,.]?\s*",
    r"^hello[,!]?\s*",
    r"^hi[,!]?\s*",
    r"^hey[,!]?\s*",
    r"^good\s+morning[,!]?\s*",
    r"^good\s+afternoon[,!]?\s*",
    r"^good\s+evening[,!]?\s*",
    r"^dear\s+support[,.]?\s*",
    r"^dear\s+team[,.]?\s*",
]
_RE_ENGLISH_FILLERS = re.compile(
    "|".join(_ENGLISH_FILLERS), re.IGNORECASE
)

# Build Thai filler pattern: sorted by length descending to prefer longer matches
_thai_sorted = sorted(THAI_FILLER_PHRASES, key=len, reverse=True)
_thai_escaped = [re.escape(p) for p in _thai_sorted]
_RE_THAI_FILLERS = re.compile("|".join(_thai_escaped))

# Trailing/leading polite particles in Thai (ครับ, ค่ะ, คะ, นะครับ, ฯลฯ)
_RE_THAI_PARTICLES_LEADING = re.compile(
    r"^(ครับ|ค่ะ|คะ|นะครับ|นะคะ|หน่อยครับ|หน่อยค่ะ|นะ)\s*"
)


def _clean_single(text: str) -> str:
    """Apply all cleaning rules to a single question string."""
    # 1. Strip Thai filler phrases (can appear multiple times)
    for _ in range(3):  # up to 3 passes to handle stacked phrases
        cleaned = _RE_THAI_FILLERS.sub(" ", text).strip()
        if cleaned == text:
            break
        text = cleaned

    # 2. Strip English filler openers
    text = _RE_ENGLISH_FILLERS.sub("", text).strip()

    # 3. Remove leading polite Thai particles that remain alone
    text = _RE_THAI_PARTICLES_LEADING.sub("", text).strip()

    # 4. Collapse repeated punctuation  (!!!! → !)
    text = _RE_REPEATED_PUNCT.sub(r"\1", text)

    # 5. Normalize whitespace
    text = _RE_WHITESPACE.sub(" ", text).strip()

    return text


def clean_questions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Stage 2 entry point.
    Adds 'clean_question' column to the dataframe while preserving original 'question'.

    Returns:
        pd.DataFrame with new column: clean_question
    """
    logger.info("Stage 2: Cleaning questions …")

    df = df.copy()
    df[FIELD_CLEAN_QUESTION] = df[FIELD_QUESTION].apply(_clean_single)

    # Log how many changed
    changed = (df[FIELD_QUESTION] != df[FIELD_CLEAN_QUESTION]).sum()
    logger.info(
        f"Stage 2 complete: {changed}/{len(df)} questions were modified by cleaning."
    )
    return df
