"""
main.py — Pipeline Orchestrator
Runs all 13 stages in sequence and optionally starts the API server.

Usage:
  python backend/main.py --input data/conversations.json
  python backend/main.py --input data/conversations.json --serve
"""

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import (
    ANALYTICS_OUTPUT_FILE,
    DEFAULT_INPUT_FILE,
    EMBEDDINGS_CACHE_FILE,
    EMBEDDINGS_IDS_CACHE_FILE,
    FAQ_OUTPUT_FILE,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    API_HOST,
    API_PORT,
    API_RELOAD,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def run_pipeline(input_file: str) -> dict:
    """
    Execute all 13 pipeline stages and return state dict for the API.

    Returns:
        {
            "faq_index": FAQSearchIndex,
            "faqs": list[dict],
            "analytics": dict,
            "valid_questions": list[str],
            "valid_embeddings": np.ndarray,
        }
    """
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("FAQ Mining Pipeline — Starting")
    logger.info("=" * 60)

    # ── Stage 1: Data Ingestion ───────────────────────────────────────────────
    from backend.data_loader import load_dataset
    raw_df = load_dataset(input_file)

    # ── Stage 2: Text Cleaning ────────────────────────────────────────────────
    from backend.text_cleaner import clean_questions
    cleaned_df = clean_questions(raw_df)

    # ── Stage 3: Question Filtering ───────────────────────────────────────────
    from backend.question_filter import filter_questions
    valid_df = filter_questions(cleaned_df)

    if len(valid_df) == 0:
        raise RuntimeError("No valid questions after filtering. Please check your dataset.")

    # ── Stage 4: Sentence Embeddings ──────────────────────────────────────────
    from backend.embedding_service import generate_embeddings
    embeddings, valid_df = generate_embeddings(
        valid_df,
        use_cache=True,
        cache_file=EMBEDDINGS_CACHE_FILE,
        ids_cache_file=EMBEDDINGS_IDS_CACHE_FILE,
    )

    # ── Stage 5: Semantic Deduplication ──────────────────────────────────────
    from backend.deduplication import deduplicate
    unique_df, full_df_with_flags = deduplicate(valid_df, embeddings)

    # Get embeddings for unique rows only
    unique_mask = ~full_df_with_flags["is_duplicate"].values
    unique_embeddings = embeddings[unique_mask]

    if len(unique_df) == 0:
        raise RuntimeError("All questions were deduplicated. Check dedup threshold.")

    # ── Stage 6: HDBSCAN Clustering ───────────────────────────────────────────
    from backend.clustering import run_clustering, filter_clusters
    clustered_df = run_clustering(unique_df, unique_embeddings)

    # ── Stage 7: Cluster Quality Filtering ────────────────────────────────────
    clustered_df = filter_clusters(clustered_df, unique_embeddings)

    # ── Stages 8–10: FAQ Generation ───────────────────────────────────────────
    from backend.faq_generator import generate_faqs
    faqs = generate_faqs(clustered_df, unique_embeddings)

    if not faqs:
        logger.warning(
            "No FAQs were generated. The dataset may be too small or too diverse. "
            "Try lowering CLUSTER_MIN_CLUSTER_SIZE in config.py."
        )

    # ── Stage 11: Build FAISS Search Index ────────────────────────────────────
    from backend.search_index import FAQSearchIndex
    faq_index = FAQSearchIndex()
    if faqs:
        faq_index.build(faqs)
        faq_index.save()

    # ── Save FAQ dataset ──────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
    with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(faqs, f, ensure_ascii=False, indent=2)
    logger.info(f"FAQ dataset saved to: {FAQ_OUTPUT_FILE}")

    # ── Stage 13: Analytics ───────────────────────────────────────────────────
    from backend.analytics import generate_analytics
    analytics = generate_analytics(raw_df, full_df_with_flags, unique_df, clustered_df, faqs)

    os.makedirs(os.path.dirname(ANALYTICS_OUTPUT_FILE), exist_ok=True)
    with open(ANALYTICS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(analytics, f, ensure_ascii=False, indent=2)
    logger.info(f"Analytics report saved to: {ANALYTICS_OUTPUT_FILE}")

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"Pipeline complete in {elapsed:.1f}s | {len(faqs)} FAQs generated")
    logger.info("=" * 60)

    # Valid questions + embeddings for the API's /similar_questions endpoint
    valid_questions = valid_df["clean_question"].tolist()
    import numpy as np
    from backend.embedding_service import l2_normalize
    valid_embeddings = l2_normalize(embeddings)

    return {
        "faq_index": faq_index,
        "faqs": faqs,
        "analytics": analytics,
        "valid_questions": valid_questions,
        "valid_embeddings": valid_embeddings,
    }


def main():
    parser = argparse.ArgumentParser(
        description="FAQ Mining Pipeline — extract FAQs from customer support conversations."
    )
    parser.add_argument(
        "--input",
        type=str,
        default=DEFAULT_INPUT_FILE,
        help="Path to input dataset (JSON or CSV).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start FastAPI server after pipeline completes.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=API_HOST,
        help="API server host.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=API_PORT,
        help="API server port.",
    )
    args = parser.parse_args()

    state = run_pipeline(args.input)

    if args.serve:
        import subprocess

        def kill_port(port: int):
            """Try to kill the process listening on the given port to avoid address conflicts."""
            try:
                if sys.platform == "win32":
                    # Find PID on Windows using netstat, then taskkill
                    netstat_cmd = f'netstat -ano | findstr :{port}'
                    out = subprocess.check_output(netstat_cmd, shell=True, text=True)
                    for line in out.strip().split("\n"):
                        if f":{port}" in line and "LISTENING" in line:
                            pid = line.strip().split()[-1]
                            subprocess.run(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            logger.info(f"Killed existing process {pid} on port {port}")
                else:
                    # Linux/Mac
                    subprocess.run(f"lsof -ti:{port} | xargs kill -9", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    logger.info(f"Killed existing processes on port {port}")
            except Exception as e:
                pass  # Ignore if no process or no permission
        
        kill_port(args.port)

        from backend.api import app, set_pipeline_state
        set_pipeline_state(
            faq_index=state["faq_index"],
            faqs=state["faqs"],
            analytics=state["analytics"],
            valid_questions=state["valid_questions"],
            valid_embeddings=state["valid_embeddings"],
        )
        import uvicorn
        logger.info(f"Starting API server at http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
