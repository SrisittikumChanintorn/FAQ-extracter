"""
data_loader.py — Stage 1: Data Ingestion
Loads JSON or CSV conversation datasets and normalizes to {question, answer} schema.
"""

import json
import logging
import os
import sys

import pandas as pd

# Add project root to path so config is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    FIELD_ANSWER,
    FIELD_QUESTION,
    RAW_ADMIN_FIELD,
    RAW_CUSTOMER_FIELD,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def load_json(file_path: str) -> pd.DataFrame:
    """Load conversations from a JSON file."""
    logger.info(f"Loading JSON dataset from: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        # Handle {"data": [...]} wrapping
        for key in ("data", "conversations", "records", "items"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            raise ValueError(
                "JSON file contains a dict but no recognized list key was found. "
                "Expected a list or a dict with key 'data'/'conversations'."
            )

    df = pd.DataFrame(data)
    logger.info(f"Loaded {len(df)} records from JSON.")
    return df


def load_csv(file_path: str) -> pd.DataFrame:
    """Load conversations from a CSV file."""
    logger.info(f"Loading CSV dataset from: {file_path}")
    df = pd.read_csv(file_path, encoding="utf-8")
    logger.info(f"Loaded {len(df)} records from CSV.")
    return df


def normalize_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize raw dataframe columns to {question, answer}.
    Supports multiple common column naming conventions.
    """
    # Try to map to standard names
    col_map = {}

    # Customer message → question
    for candidate in (RAW_CUSTOMER_FIELD, "customer", "question", "user_message", "query", "input"):
        if candidate in df.columns:
            col_map[candidate] = FIELD_QUESTION
            break

    # Admin reply → answer
    for candidate in (RAW_ADMIN_FIELD, "admin", "answer", "response", "reply", "output"):
        if candidate in df.columns:
            col_map[candidate] = FIELD_ANSWER
            break

    if FIELD_QUESTION not in col_map.values():
        raise ValueError(
            f"Could not find a question column. Available columns: {list(df.columns)}"
        )
    if FIELD_ANSWER not in col_map.values():
        raise ValueError(
            f"Could not find an answer column. Available columns: {list(df.columns)}"
        )

    df = df.rename(columns=col_map)
    df = df[[FIELD_QUESTION, FIELD_ANSWER]].copy()

    # Convert to string and strip whitespace
    df[FIELD_QUESTION] = df[FIELD_QUESTION].astype(str).str.strip()
    df[FIELD_ANSWER] = df[FIELD_ANSWER].astype(str).str.strip()

    # Track original row index for traceability
    df = df.reset_index(drop=True)
    df["row_id"] = df.index

    return df


def reject_empty(df: pd.DataFrame) -> pd.DataFrame:
    """Remove rows where question or answer is empty / null / placeholder."""
    before = len(df)
    invalid_values = {"", "nan", "none", "null", "n/a", "na"}

    mask = (
        df[FIELD_QUESTION].str.lower().isin(invalid_values)
        | df[FIELD_ANSWER].str.lower().isin(invalid_values)
        | df[FIELD_QUESTION].isna()
        | df[FIELD_ANSWER].isna()
    )
    df = df[~mask].copy().reset_index(drop=True)
    rejected = before - len(df)
    logger.info(f"Rejected {rejected} empty rows. {len(df)} rows remaining.")
    return df


def load_dataset(file_path: str) -> pd.DataFrame:
    """
    Main entry point for Stage 1.
    Auto-detects file format, normalizes schema, and rejects empty rows.

    Returns:
        pd.DataFrame with columns: [question, answer, row_id]
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Dataset file not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".json":
        df = load_json(file_path)
    elif ext == ".csv":
        df = load_csv(file_path)
    else:
        raise ValueError(f"Unsupported file format: '{ext}'. Use .json or .csv")

    df = normalize_schema(df)
    df = reject_empty(df)

    logger.info(f"Stage 1 complete: {len(df)} valid conversation pairs loaded.")
    return df
