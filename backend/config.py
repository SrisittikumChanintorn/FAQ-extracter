"""
config.py — Global Configuration File
All tunable variables, paths, and constants for the FAQ Mining System.
"""

import os

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

# Input data
DEFAULT_INPUT_FILE = os.path.join(DATA_DIR, "conversations.json")

# Output files
FAQ_OUTPUT_FILE = os.path.join(DATA_DIR, "faqs.json")
ANALYTICS_OUTPUT_FILE = os.path.join(DATA_DIR, "analytics_report.json")
FAISS_INDEX_FILE = os.path.join(DATA_DIR, "faiss.index")
FAISS_META_FILE = os.path.join(DATA_DIR, "faq_index_meta.json")

# ─────────────────────────────────────────────
# STAGE 1 — DATA INGESTION
# ─────────────────────────────────────────────
# Expected field names in the raw dataset
RAW_CUSTOMER_FIELD = "customer_message"
RAW_ADMIN_FIELD = "admin_reply"

# Normalized field names used internally
FIELD_QUESTION = "question"
FIELD_ANSWER = "answer"
FIELD_CLEAN_QUESTION = "clean_question"

# ─────────────────────────────────────────────
# STAGE 2 — TEXT CLEANING
# ─────────────────────────────────────────────
# Thai greeting / filler phrases to strip
THAI_FILLER_PHRASES = [
    "สวัสดีครับ",
    "สวัสดีค่ะ",
    "สวัสดี",
    "ขอถามหน่อย",
    "ขอถามหน่อยครับ",
    "ขอถามหน่อยค่ะ",
    "สอบถามครับ",
    "สอบถามค่ะ",
    "สอบถาม",
    "มีเรื่องสอบถาม",
    "มีเรื่องสอบถามครับ",
    "มีเรื่องสอบถามค่ะ",
    "ขอสอบถามหน่อยครับ",
    "ขอสอบถามหน่อยค่ะ",
    "ขอสอบถาม",
    "รบกวนสอบถามครับ",
    "รบกวนสอบถามค่ะ",
    "hello",
    "hi",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
]

# ─────────────────────────────────────────────
# STAGE 3 — QUESTION FILTERING
# ─────────────────────────────────────────────
MIN_QUESTION_LENGTH = 8        # Minimum characters after cleaning (noise reduction)
MAX_QUESTION_LENGTH = 1000    # Maximum characters (to reject ultra-long noise)

# Regex patterns for emoji detection
EMOJI_PATTERN = (
    "["
    "\U0001F600-\U0001F64F"   # emoticons
    "\U0001F300-\U0001F5FF"   # symbols & pictographs
    "\U0001F680-\U0001F6FF"   # transport & map
    "\U0001F1E0-\U0001F1FF"   # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\\U0001f926-\\U0001f937"
    "\U00010000-\U0010ffff"
    "\u2640-\u2642"
    "\u2600-\u2B55"
    "\u200d"
    "\u23cf"
    "\u23e9"
    "\u231a"
    "\ufe0f"
    "\u3030"
    "]+"
)

# ─────────────────────────────────────────────
# STAGE 4 — EMBEDDING (BAAI/bge-m3)
# ─────────────────────────────────────────────
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_BATCH_SIZE = 32             # bge-m3 is larger; use smaller batches
EMBEDDING_DIMENSION = 1024            # bge-m3 output dimension
EMBEDDING_DEVICE = "cpu"              # Set to "cuda" if GPU available
EMBEDDING_SHOW_PROGRESS = True
# bge-m3 supports three retrieval methods; we use dense (default).
# Set to True to prepend instruction prefixes (recommended for asymmetric retrieval).
EMBEDDING_USE_INSTRUCTION = True
EMBEDDING_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
EMBEDDING_PASSAGE_PREFIX = ""         # bge-m3 passages need no prefix

# ─────────────────────────────────────────────
# LLM BATCH FAQ EXTRACTION + GROUPS
# ─────────────────────────────────────────────
# Number of data splits (fewer = faster test runs; increase for production).
FAQ_N_SPLITS = 3
# Conversations per LLM call (micro-batch). Smaller = faster per call.
FAQ_EXTRACTION_BATCH_SIZE = 5
# Max FAQ pairs to extract per micro-batch (lower = faster LLM response).
FAQ_PER_BATCH = 6
# Hard cap on FAQ items saved after quality ranking (UI can override up to FAQ_MAX_OUTPUT_CAP).
FAQ_MAX_OUTPUT_DEFAULT = 200
FAQ_MAX_OUTPUT_CAP = 500
# Max tokens for LLM response (lower = faster).
FAQ_EXTRACTION_NUM_PREDICT = 1200
# When merging batches: question similarity above this → same FAQ (merge mention_count).
MERGE_QUESTION_SIMILARITY_THRESHOLD = 0.88
# When merging batches: group name match (string or embedding similarity) min ratio.
MERGE_GROUP_NAME_MIN_SIMILARITY = 0.7
# Use embedding similarity for group names (more accurate for Thai synonyms).
MERGE_GROUP_USE_EMBEDDING = True

# LLM (Ollama) for batch extract + group naming
TOPIC_NAMER_MODEL = os.environ.get("LLM_MODEL", "scb10x/llama3.1-typhoon2-8b-instruct")
TOPIC_NAMER_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
# CPU runs slowly: use no timeout by default (OLLAMA_TIMEOUT=none). Set e.g. 3600 for 1h limit.
_timeout_env = os.environ.get("OLLAMA_TIMEOUT", "none")
OLLAMA_TIMEOUT = None if (not _timeout_env or str(_timeout_env).strip().lower() == "none") else int(_timeout_env)
OLLAMA_RETRY_COUNT = int(os.environ.get("OLLAMA_RETRY_COUNT", "3"))   # retries per LLM call
OLLAMA_RETRY_DELAY_SEC = float(os.environ.get("OLLAMA_RETRY_DELAY_SEC", "10.0"))  # delay between retries

REPRESENTATIVE_Q_COUNT = 5          # Questions per group used for search index

# ─────────────────────────────────────────────
# FAISS SEARCH INDEX
# ─────────────────────────────────────────────
FAISS_TOP_K_DEFAULT = 5             # Default number of results per query
FAISS_USE_GPU = False               # Set True if faiss-gpu is installed
SEARCH_MAX_TOP_K = 50               # Hard cap on top_k

# ─────────────────────────────────────────────
# STAGE 12 — API SERVER
# ─────────────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 8000
API_RELOAD = True                   # Set False in production
API_TITLE = "FAQ Mining System API"
API_VERSION = "2.0.0"
API_DESCRIPTION = "Production-grade FAQ extraction from Thai customer support conversations."

CORS_ORIGINS = ["*"]  # Allow all origins for local development

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
