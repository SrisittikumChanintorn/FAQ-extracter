"""
main.py — Optimized FAQ extraction pipeline (LLM-centric batch + merge)

Pipeline:
  1. Load data
  2. Clean text
  3. Filter questions
  4. Split into n_splits; per split: LLM extracts FAQs + assigns group_name (micro-batches)
  5. Merge batch results in pairs: merge groups by name, dedupe FAQs by similarity, sum mention_count
  6. Build search index (FAISS from group questions)
  7. Save output + analytics
  8. (Optional) Serve API

Usage:
  python backend/main.py --input data/conversations.json
  python backend/main.py --input data/conversations.json --serve
"""

from __future__ import annotations

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
    FAQ_N_SPLITS,
    FAQ_OUTPUT_FILE,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    API_HOST,
    API_PORT,
    REPRESENTATIVE_Q_COUNT,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)


def _groups_to_canonical_schema(groups: list[dict]) -> list[dict]:
    """Convert merged groups (group_name, faqs) to API/frontend schema with group_id, total_faqs, representative_questions, etc."""
    canonical = []
    for i, g in enumerate(groups):
        faqs = g.get("faqs", [])
        rep_qs = [f.get("question", "") for f in faqs[:REPRESENTATIVE_Q_COUNT] if f.get("question")]
        if not rep_qs:
            rep_qs = [g.get("group_name") or "Other"]
        support_count = sum(f.get("mention_count", 1) for f in faqs)
        canonical.append({
            "group_id": i,
            "cluster_id": i,
            "group_name": (g.get("group_name") or "").strip() or "Other",
            "total_faqs": len(faqs),
            "total_questions": len(faqs),
            "support_count": support_count,
            "faqs": faqs,
            "representative_questions": rep_qs,
            "canonical_question": rep_qs[0] if rep_qs else "",
            "canonical_answer": faqs[0].get("answer", "") if faqs else "",
            "faq_question": rep_qs[0] if rep_qs else "",
            "faq_answer": faqs[0].get("answer", "") if faqs else "",
            "suggested_admin_reply": faqs[0].get("answer", "") if faqs else "",
        })
    canonical.sort(key=lambda x: x["support_count"], reverse=True)
    for i, g in enumerate(canonical):
        g["group_id"] = i
        g["cluster_id"] = i
    return canonical


def run_pipeline(
    input_file: str,
    progress_callback=None,
    n_splits_override=None,
    batch_size_override=None,
    max_faqs: int | None = None,
) -> dict:
    """
    Run optimized pipeline: load → clean → filter → batch extract (LLM) → merge → index → save.
    progress_callback(stage, stage_name, message) is optional for UI progress.
    n_splits_override / batch_size_override: if provided, override config defaults.
      If None, auto-calculate based on dataset size for optimal results.
    max_faqs: cap total FAQ items after quality filtering (default from config).
    Returns state dict for API.
    """
    import numpy as np

    def _prog(stage: int, name: str, msg: str):
        if progress_callback:
            progress_callback(stage, name, msg)
        logger.info(msg)

    start_time = time.time()
    logger.info("=" * 70)
    logger.info("FAQ Pipeline (LLM batch + merge)")
    logger.info("=" * 70)

    # ── 1. Load ─────────────────────────────────────────────────────────────
    _prog(1, "Loading dataset", "Stage 1: Loading dataset…")
    from backend.data_loader import load_dataset
    raw_df = load_dataset(input_file)
    _prog(1, "Loading dataset", f"  → Loaded {len(raw_df)} records.")

    # ── 2. Clean ────────────────────────────────────────────────────────────
    _prog(2, "Cleaning text", "Stage 2: Cleaning text…")
    from backend.text_cleaner import clean_questions
    cleaned_df = clean_questions(raw_df)
    _prog(2, "Cleaning text", f"  → {len(cleaned_df)} rows after cleaning.")

    # ── 3. Filter ───────────────────────────────────────────────────────────
    _prog(3, "Filtering questions", "Stage 3: Filtering questions…")
    from backend.question_filter import filter_questions
    valid_df = filter_questions(cleaned_df)
    _prog(3, "Filtering questions", f"  → {len(valid_df)} valid questions.")

    if len(valid_df) == 0:
        raise RuntimeError("No valid questions after filtering. Check your dataset.")

    # ── 4. Batch extract (LLM: FAQ + group per split) ────────────────────────
    from backend.batch_extractor import run_all_batches
    from backend.config import FAQ_EXTRACTION_BATCH_SIZE

    # Determine batch parameters: use overrides, or auto-calculate from row count
    row_count = len(valid_df)
    if n_splits_override is not None:
        actual_n_splits = max(1, n_splits_override)
    else:
        # Auto: 1 split per ~50 rows, min 1, max 10
        actual_n_splits = max(1, min(10, row_count // 50)) if row_count > 0 else FAQ_N_SPLITS

    if batch_size_override is not None:
        actual_batch_size = max(1, batch_size_override)
    else:
        # Auto: 3-8 rows per batch depending on dataset size
        if row_count <= 30:
            actual_batch_size = 3
        elif row_count <= 100:
            actual_batch_size = 5
        else:
            actual_batch_size = FAQ_EXTRACTION_BATCH_SIZE

    _prog(4, "LLM batch extraction", f"Stage 4: LLM extracting FAQs + groups per batch… (splits={actual_n_splits}, batch_size={actual_batch_size})")
    batch_results = run_all_batches(
        valid_df,
        n_splits=actual_n_splits,
        micro_batch_size=actual_batch_size,
        progress_callback=progress_callback,
    )

    if not batch_results or all(not r for r in batch_results):
        raise RuntimeError(
            "No FAQs extracted. Ensure Ollama is running and the model is loaded. "
            "Run: ollama pull <model-name> (and make sure the Ollama service is running)."
        )
    _prog(4, "LLM batch extraction", f"  → {len(batch_results)} batches ready to merge.")

    # ── 5. Merge batches (pairwise until one) ─────────────────────────────────
    _prog(5, "Merging batches", "Stage 5: Merging batch results (dedupe, mention count)…")
    from backend.batch_merger import merge_all_batch_results
    merged_groups = merge_all_batch_results(batch_results)

    if not merged_groups:
        raise RuntimeError("Merge produced no groups. Check LLM output format.")

    from backend.faq_quality import filter_and_cap_groups
    merged_groups = filter_and_cap_groups(merged_groups, max_faqs)
    if not merged_groups:
        raise RuntimeError(
            "No FAQs left after quality filtering. Try lowering filters or check your dataset."
        )

    groups = _groups_to_canonical_schema(merged_groups)
    total_faq_items = sum(g["total_faqs"] for g in groups)
    _prog(5, "Merging batches", f"  → {len(groups)} groups, {total_faq_items} FAQ items.")

    # ── 6. Search index ──────────────────────────────────────────────────────
    _prog(6, "Building search index", "Stage 6: Building FAISS search index…")
    from backend.search_index import FAQSearchIndex
    faq_index = FAQSearchIndex()
    faq_index.build(groups)
    faq_index.save()

    # ── 7. Save output ───────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
    with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"total_groups": len(groups), "groups": groups}, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved: {FAQ_OUTPUT_FILE}")

    # ── 8. Analytics (simplified) ────────────────────────────────────────────
    _prog(7, "Analytics", "Stage 7: Generating analytics…")
    from backend.analytics import generate_analytics_simple
    analytics = generate_analytics_simple(raw_df, groups)

    os.makedirs(os.path.dirname(ANALYTICS_OUTPUT_FILE), exist_ok=True)
    with open(ANALYTICS_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(analytics, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    logger.info("=" * 70)
    logger.info(f"Pipeline complete in {elapsed:.1f}s | {len(groups)} groups | {total_faq_items} FAQ items")
    logger.info("=" * 70)

    # For /similar_questions: collect all questions from groups and embed (optional, can be empty)
    valid_questions = []
    try:
        from backend.embedding_service import encode_texts, l2_normalize
        for g in groups:
            for f in g.get("faqs", []):
                q = f.get("question", "")
                if q:
                    valid_questions.append(q)
        if valid_questions:
            raw_embs = encode_texts(valid_questions[:500], is_query=False, show_progress=False)
            valid_embeddings = l2_normalize(raw_embs)
        else:
            valid_embeddings = np.empty((0, 0))
    except Exception:
        valid_questions = []
        valid_embeddings = np.empty((0, 0))

    return {
        "faq_index": faq_index,
        "groups": groups,
        "faqs": groups,
        "analytics": analytics,
        "valid_questions": valid_questions,
        "valid_embeddings": valid_embeddings,
    }


def main():
    parser = argparse.ArgumentParser(description="FAQ extraction pipeline (LLM batch + merge)")
    parser.add_argument("--input", type=str, default=DEFAULT_INPUT_FILE)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", type=str, default=API_HOST)
    parser.add_argument("--port", type=int, default=API_PORT)
    args = parser.parse_args()

    state = run_pipeline(args.input)

    if args.serve:
        import subprocess

        def kill_port(port: int):
            try:
                if sys.platform == "win32":
                    out = subprocess.check_output(
                        f"netstat -ano | findstr :{port}", shell=True, text=True
                    )
                    for line in out.strip().split("\n"):
                        if f":{port}" in line and "LISTENING" in line:
                            pid = line.strip().split()[-1]
                            subprocess.run(
                                f"taskkill /F /PID {pid}", shell=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            )
                else:
                    subprocess.run(
                        f"lsof -ti:{port} | xargs kill -9", shell=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
            except Exception:
                pass

        kill_port(args.port)

        from backend.api import app, set_pipeline_state
        set_pipeline_state(
            faq_index=state["faq_index"],
            faqs=state["groups"],
            analytics=state["analytics"],
            valid_questions=state["valid_questions"],
            valid_embeddings=state["valid_embeddings"],
        )
        import uvicorn

        base = f"http://{args.host}:{args.port}"
        print("\n" + "=" * 70)
        print("  FAQ MINING SYSTEM — Server Starting")
        print("=" * 70)
        print(f"  Status:  starting (after ready → 200 OK)")
        print(f"  Port:    {args.port}")
        print(f"  URL:     {base}")
        print("  " + "-" * 66)
        print(f"  ➜  Copy & open:  {base}")
        print(f"  ➜  API Docs:     {base}/docs")
        print(f"  ➜  Health:       {base}/health  (expect 200 when ready)")
        print("=" * 70 + "\n")

        logger.info(f"Starting server at {base} (port {args.port})")
        uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
