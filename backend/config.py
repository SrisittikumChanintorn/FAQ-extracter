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
EMBEDDINGS_CACHE_FILE = os.path.join(DATA_DIR, "embeddings_cache.npy")
EMBEDDINGS_IDS_CACHE_FILE = os.path.join(DATA_DIR, "embeddings_ids_cache.npy")
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
FIELD_CLUSTER_ID = "cluster_id"
FIELD_IS_DUPLICATE = "is_duplicate"
FIELD_CANONICAL_ID = "canonical_id"
FIELD_DUPLICATE_COUNT = "duplicate_count"

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
MIN_QUESTION_LENGTH = 5       # Minimum characters after cleaning
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
# STAGE 4 — EMBEDDING
# ─────────────────────────────────────────────
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_BATCH_SIZE = 128
EMBEDDING_DIMENSION = 384
EMBEDDING_DEVICE = "cpu"          # Set to "cuda" if GPU available
EMBEDDING_SHOW_PROGRESS = True

# ─────────────────────────────────────────────
# STAGE 5 — SEMANTIC DEDUPLICATION
# ─────────────────────────────────────────────
DEDUP_SIMILARITY_THRESHOLD = 0.99   # Cosine similarity above which → duplicate
DEDUP_CHUNK_SIZE = 5000             # Process dedup in chunks for large datasets

# ─────────────────────────────────────────────
# STAGE 6 — CLUSTERING (HDBSCAN)
# ─────────────────────────────────────────────
CLUSTER_MIN_CLUSTER_SIZE = 2        # Minimum points to form a cluster
CLUSTER_MIN_SAMPLES = 2             # Controls how conservative HDBSCAN is
CLUSTER_METRIC = "euclidean"        # Used on L2-normalised embeddings ≡ cosine
CLUSTER_SELECTION_METHOD = "eom"    # "eom" or "leaf"

# ─────────────────────────────────────────────
# STAGE 7 — CLUSTER QUALITY FILTERING
# ─────────────────────────────────────────────
CLUSTER_MIN_SIZE_THRESHOLD = 2      # Clusters smaller than this are discarded
CLUSTER_MAX_INTRA_DISTANCE = 1.5    # Max avg pairwise L2 distance (0-2 for unit vecs, ~0.7 cosine sim at 1.5)

# ─────────────────────────────────────────────
# STAGE 9 — ANSWER EXTRACTION
# ─────────────────────────────────────────────
ANSWER_SIMILARITY_THRESHOLD = 0.70  # If max answer similarity < this → summarize
ANSWER_MIN_LENGTH = 5               # Minimum answer length in characters

# ─────────────────────────────────────────────
# STAGE 11 — FAISS SEARCH INDEX
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
API_VERSION = "1.0.0"
API_DESCRIPTION = "Production-grade FAQ extraction from customer support conversations."

CORS_ORIGINS = ["*"]  # Allow all origins for local development

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
