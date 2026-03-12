"""
api.py — Stage 12: FastAPI REST Service + Static Frontend
Endpoints:
  GET  /                     - Serve frontend UI
  GET  /manual               - Serve user manual UI
  GET  /health               - Liveness check
  GET  /faqs                 - Full FAQ list (optional ?limit=N)
  POST /search               - Semantic FAQ search
  POST /similar_questions    - Historical question similarity search
  GET  /clusters             - Cluster metadata
  GET  /analytics            - Analytics report
  GET  /visualization-data   - 3D PCA projection for cluster visualization
  POST /upload               - Upload input file (xlsx / csv / json)
  POST /preview-data         - Read uploaded file headers and top 5 rows
  POST /apply-mapping        - Rename chosen columns and save ready for processing
  POST /run-pipeline         - Trigger pipeline in background thread
  GET  /pipeline-status      - Poll pipeline progress
  POST /faqs/relabel         - Relabel FAQs to a different cluster
  POST /faqs/delete          - Delete FAQs by index
"""

import json
import logging
import os
import shutil
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import (
    ANALYTICS_OUTPUT_FILE,
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    CORS_ORIGINS,
    DEFAULT_INPUT_FILE,
    FAISS_INDEX_FILE,
    FAISS_META_FILE,
    FAISS_TOP_K_DEFAULT,
    FAQ_OUTPUT_FILE,
    FRONTEND_DIR,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_LEVEL,
    SEARCH_MAX_TOP_K,
    UPLOAD_DIR,
)

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
logger = logging.getLogger(__name__)

# ── Global State ──────────────────────────────────────────────────────────────

_faq_index = None
_faqs: list[dict] = []
_analytics_report: dict = {}
_valid_questions: list[str] = []
_valid_embeddings: np.ndarray = np.empty((0,))

# ── Pipeline State (thread-safe progress tracking) ────────────────────────────

_pipeline_lock = threading.Lock()
_pipeline_state = {
    "status": "idle",          # idle | running | done | error
    "stage": 0,
    "total_stages": 13,
    "stage_name": "",
    "logs": [],
    "started_at": None,
    "finished_at": None,
    "error": None,
    "faq_count": 0,
    "input_file": None,
}

ALLOWED_EXTENSIONS = {".json", ".csv", ".xlsx", ".xls"}


def _reset_pipeline_state(input_file: str):
    with _pipeline_lock:
        _pipeline_state.update({
            "status": "running",
            "stage": 0,
            "stage_name": "Starting…",
            "logs": [f"▶ Starting pipeline on: {input_file}"],
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "faq_count": 0,
            "input_file": input_file,
        })


def _log(msg: str, stage: int = None, stage_name: str = None):
    with _pipeline_lock:
        _pipeline_state["logs"].append(msg)
        if stage is not None:
            _pipeline_state["stage"] = stage
        if stage_name is not None:
            _pipeline_state["stage_name"] = stage_name
    logger.info(msg)


def set_pipeline_state(faq_index, faqs, analytics, valid_questions, valid_embeddings):
    """Called by main.py after pipeline completes to inject state into API."""
    global _faq_index, _faqs, _analytics_report, _valid_questions, _valid_embeddings
    _faq_index = faq_index
    _faqs = faqs
    _analytics_report = analytics
    _valid_questions = valid_questions
    _valid_embeddings = valid_embeddings
    logger.info(f"API state updated: {len(_faqs)} FAQs, {len(_valid_questions)} historical questions.")


# ── Pipeline Runner (runs in a background thread) ─────────────────────────────

def _run_pipeline_thread(input_file: str):
    global _faq_index, _faqs, _analytics_report, _valid_questions, _valid_embeddings

    try:
        _reset_pipeline_state(input_file)

        _log("Stage 1: Loading dataset…", stage=1, stage_name="Loading dataset")
        from backend.data_loader import load_dataset
        raw_df = load_dataset(input_file)
        _log(f"  → Loaded {len(raw_df)} records.")

        _log("Stage 2: Cleaning text…", stage=2, stage_name="Cleaning text")
        from backend.text_cleaner import clean_questions
        cleaned_df = clean_questions(raw_df)
        _log(f"  → {len(cleaned_df)} rows after cleaning.")

        _log("Stage 3: Filtering questions…", stage=3, stage_name="Filtering questions")
        from backend.question_filter import filter_questions
        valid_df = filter_questions(cleaned_df)
        _log(f"  → {len(valid_df)} valid questions retained.")

        if len(valid_df) == 0:
            raise RuntimeError("No valid questions after filtering. Check your dataset.")

        _log("Stage 4: Generating sentence embeddings…", stage=4, stage_name="Generating embeddings")
        from backend.embedding_service import generate_embeddings
        from backend.config import EMBEDDINGS_CACHE_FILE, EMBEDDINGS_IDS_CACHE_FILE
        embeddings, valid_df = generate_embeddings(
            valid_df, use_cache=True,
            cache_file=EMBEDDINGS_CACHE_FILE,
            ids_cache_file=EMBEDDINGS_IDS_CACHE_FILE,
        )
        _log(f"  → Embeddings shape: {embeddings.shape}")

        _log("Stage 5: Deduplicating questions…", stage=5, stage_name="Deduplicating")
        from backend.deduplication import deduplicate
        unique_df, full_df_with_flags = deduplicate(valid_df, embeddings)
        unique_mask = ~full_df_with_flags["is_duplicate"].values
        unique_embeddings = embeddings[unique_mask]
        _log(f"  → {len(unique_df)} unique questions after dedup.")

        if len(unique_df) == 0:
            raise RuntimeError("All questions were deduplicated. Adjust dedup threshold.")

        _log("Stage 6: Clustering with HDBSCAN…", stage=6, stage_name="HDBSCAN clustering")
        from backend.clustering import run_clustering
        clustered_df = run_clustering(unique_df, unique_embeddings)

        _log("Stage 7: Filtering cluster quality…", stage=7, stage_name="Cluster quality filter")
        from backend.clustering import filter_clusters
        clustered_df = filter_clusters(clustered_df, unique_embeddings)
        n_clusters = clustered_df["cluster_id"].nunique() if len(clustered_df) > 0 else 0
        _log(f"  → {n_clusters} quality clusters retained.")

        _log("Stages 8–10: Generating FAQs…", stage=8, stage_name="FAQ generation")
        from backend.faq_generator import generate_faqs
        faqs = generate_faqs(clustered_df, unique_embeddings)
        _log(f"  → {len(faqs)} FAQs generated.")

        _log("Stage 11: Building FAISS search index…", stage=11, stage_name="Building search index")
        from backend.search_index import FAQSearchIndex
        faq_index = FAQSearchIndex()
        if faqs:
            faq_index.build(faqs)
            faq_index.save()

        _log("Saving FAQ dataset…", stage=12, stage_name="Saving outputs")
        os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
        with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(faqs, f, ensure_ascii=False, indent=2)

        _log("Stage 13: Running analytics…", stage=13, stage_name="Analytics")
        from backend.analytics import generate_analytics
        analytics = generate_analytics(raw_df, full_df_with_flags, unique_df, clustered_df, faqs)
        os.makedirs(os.path.dirname(ANALYTICS_OUTPUT_FILE), exist_ok=True)
        with open(ANALYTICS_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(analytics, f, ensure_ascii=False, indent=2)

        # Inject results into global API state
        valid_questions = valid_df["clean_question"].tolist()
        from backend.embedding_service import l2_normalize
        valid_embs = l2_normalize(embeddings)
        set_pipeline_state(faq_index, faqs, analytics, valid_questions, valid_embs)

        # Zero-Effect Optimization: Aggressively free memory of large DataFrames
        import gc
        try:
            del raw_df, valid_df, embeddings, unique_df, full_df_with_flags, clustered_df
        except UnboundLocalError:
            pass
        gc.collect()

        _log(f"✅ Pipeline complete! {len(faqs)} FAQs generated.")
        with _pipeline_lock:
            _pipeline_state["status"] = "done"
            _pipeline_state["faq_count"] = len(faqs)
            _pipeline_state["finished_at"] = time.time()

    except Exception as exc:
        err_msg = f"❌ Pipeline failed: {exc}"
        _log(err_msg)
        with _pipeline_lock:
            _pipeline_state["status"] = "error"
            _pipeline_state["error"] = str(exc)
            _pipeline_state["finished_at"] = time.time()
        logger.error(traceback.format_exc())


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: load any pre-built FAISS index and FAQ data from disk."""
    global _faq_index, _faqs, _analytics_report

    from backend.search_index import FAQSearchIndex
    index = FAQSearchIndex()
    loaded = index.load(FAISS_INDEX_FILE, FAISS_META_FILE)
    if loaded:
        _faq_index = index
        _faqs = index.faqs

    if os.path.isfile(ANALYTICS_OUTPUT_FILE):
        with open(ANALYTICS_OUTPUT_FILE, "r", encoding="utf-8") as f:
            _analytics_report = json.load(f)

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    if _faqs:
        logger.info(f"API ready: {len(_faqs)} FAQs loaded from disk.")
    else:
        logger.warning("No pre-built FAQ data found. Upload a file and run the pipeline from the UI.")

    yield


# ── App Factory ────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Pydantic models ────────────────────────────────────────────────────────

    class SearchRequest(BaseModel):
        query: str = Field(..., min_length=1, max_length=500)
        top_k: int = Field(default=FAISS_TOP_K_DEFAULT, ge=1, le=SEARCH_MAX_TOP_K)

    class SimilarQuestionsRequest(BaseModel):
        query: str = Field(..., min_length=1, max_length=500)
        top_k: int = Field(default=FAISS_TOP_K_DEFAULT, ge=1, le=SEARCH_MAX_TOP_K)

    class RunPipelineRequest(BaseModel):
        input_file: str = Field(
            default="",
            description="Path to input file. Leave empty to use last uploaded file or default dataset.",
        )

    class DeleteFAQsRequest(BaseModel):
        indices: list[int] = Field(..., description="List of FAQ indices (0-based) to delete.")

    class RelabelFAQsRequest(BaseModel):
        indices: list[int] = Field(..., description="List of FAQ indices (0-based) to relabel.")
        new_cluster_id: int = Field(..., description="The ID of the new cluster/group.")

    class PreviewDataRequest(BaseModel):
        file_path: str = Field(..., description="Absolute path of the uploaded file to preview.")

    class ApplyMappingRequest(BaseModel):
        file_path: str = Field(..., description="Absolute path of the uploaded file.")
        customer_col: str = Field(..., description="Name of the column containing customer questions.")
        admin_col: str = Field(..., description="Name of the column containing admin answers.")

    class SaveUploadedDataRequest(BaseModel):
        data: list[dict] = Field(..., description="List of dictionaries representing the edited dataset rows.")

    # ── Helper ─────────────────────────────────────────────────────────────────

    def _require_index():
        if _faq_index is None or not _faq_index.is_ready:
            raise HTTPException(
                status_code=503,
                detail="FAQ index not ready. Please upload a file and run the pipeline from the UI.",
            )

    def _pca_3d(X: np.ndarray) -> np.ndarray:
        """Reduce embedding matrix to 3 dimensions using numpy SVD (no sklearn required)."""
        n = len(X)
        if n < 2:
            return np.zeros((n, 3))
        X_c = X - X.mean(axis=0)
        # Number of components is min(n-1, features, 3)
        k = min(n - 1, X_c.shape[1], 3)
        _, _, Vt = np.linalg.svd(X_c, full_matrices=False)
        coords = X_c @ Vt[:k].T          # shape (n, k)
        if k < 3:                         # pad with zeros if fewer than 3 components
            pad = np.zeros((n, 3 - k))
            coords = np.hstack([coords, pad])
        return coords

    # ── System Endpoints ───────────────────────────────────────────────────────

    @app.get("/health", tags=["System"])
    async def health():
        return {
            "status": "ok",
            "faq_count": len(_faqs),
            "index_ready": _faq_index is not None and _faq_index.is_ready,
            "pipeline_status": _pipeline_state["status"],
        }

    @app.get("/manual", tags=["System"])
    async def serve_manual():
        """Serve the HTML user manual page."""
        pth = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "manual.html")
        if not os.path.exists(pth):
            raise HTTPException(404, "manual.html not found.")
        return FileResponse(pth)

    # ── Data Upload & Mapping ──────────────────────────────────────────────────

    @app.post("/upload", tags=["Pipeline"])
    async def upload_file(file: UploadFile = File(...)):
        """Accept an Excel (.xlsx/.xls), CSV (.csv), or JSON (.json) file."""
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        dest = os.path.join(UPLOAD_DIR, f"input{ext}")
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)

        logger.info(f"Uploaded file saved to: {dest}")
        return {
            "message": "File uploaded successfully.",
            "filename": file.filename,
            "saved_path": dest,
            "extension": ext,
        }

    @app.post("/preview-data", tags=["Pipeline"])
    async def preview_data(req: PreviewDataRequest):
        """Read an uploaded file and return its columns + top 5 sample rows for UI mapping."""
        if not os.path.isfile(req.file_path):
            raise HTTPException(404, "Uploaded file not found.")

        try:
            from backend.data_loader import load_support_data
            # Load raw dataframe (without strict validation)
            df = load_support_data(req.file_path, validate=False)
            if df.empty:
                raise ValueError("File is empty.")
                
            cols = [str(c) for c in df.columns]
            sample_df = df.head(5).fillna("")
            rows = sample_df.to_dict(orient="records")
            return {"columns": cols, "rows": rows}
        except Exception as e:
            logger.error(f"Failed to preview data: {e}")
            raise HTTPException(400, f"Cannot read file: {e}")

    @app.post("/apply-mapping", tags=["Pipeline"])
    async def apply_mapping(req: ApplyMappingRequest):
        """Extract user-mapped columns, rename them, and overwrite as the parsed format."""
        if not os.path.isfile(req.file_path):
            raise HTTPException(404, "Uploaded file not found.")

        try:
            from backend.data_loader import load_support_data
            df = load_support_data(req.file_path, validate=False)
            if req.customer_col not in df.columns or req.admin_col not in df.columns:
                raise ValueError(f"Columns '{req.customer_col}' or '{req.admin_col}' missing from file.")

            # Map to required format
            df_mapped = df[[req.customer_col, req.admin_col]].copy()
            df_mapped.rename(columns={
                req.customer_col: "customer_message", 
                req.admin_col: "admin_reply"
            }, inplace=True)
            
            # Save mapped data back out. We always save mapping results as JSON for safety.
            ext = os.path.splitext(req.file_path)[1].lower()
            mapped_path = os.path.join(UPLOAD_DIR, "input_mapped.json")
            
            # Using records orient to be standard JSON
            out_records = df_mapped.to_dict(orient="records")
            with open(mapped_path, "w", encoding="utf-8") as f:
                json.dump(out_records, f, ensure_ascii=False, indent=2)

            return {
                "message": "Mapping applied successfully. Ready for processing.",
                "mapped_file": mapped_path,
                "row_count": len(df_mapped)
            }
        except Exception as e:
            logger.error(f"Failed to apply mapping: {e}")
            raise HTTPException(400, f"Mapping failed: {e}")

    @app.get("/uploaded-data", tags=["Data Manipulation"])
    async def get_uploaded_data():
        """Fetch the most recently uploaded raw data for frontend manipulation."""
        # Check mapped file first, then raw uploads
        candidates = [
            os.path.join(UPLOAD_DIR, "input_mapped.json"),
            os.path.join(UPLOAD_DIR, "input.json"),
            os.path.join(UPLOAD_DIR, "input.csv"),
            os.path.join(UPLOAD_DIR, "input.xlsx"),
            os.path.join(UPLOAD_DIR, "input.xls")
        ]
        
        target_file = None
        for cand in candidates:
            if os.path.isfile(cand):
                target_file = cand
                break
                
        if not target_file:
            raise HTTPException(404, "No uploaded data found.")
            
        try:
            from backend.data_loader import load_support_data
            df = load_support_data(target_file, validate=False)
            df.fillna("", inplace=True)
            return {"data": df.to_dict(orient="records")}
        except Exception as e:
            logger.error(f"Failed to load uploaded data: {e}")
            raise HTTPException(500, f"Failed to load uploaded data: {e}")

    @app.post("/save-uploaded-data", tags=["Data Manipulation"])
    async def save_uploaded_data(req: SaveUploadedDataRequest):
        """Save frontend manipulated data back into a format ready for the pipeline."""
        import pandas as pd
        if not req.data:
            raise HTTPException(400, "No data provided to save.")
            
        try:
            df = pd.DataFrame(req.data)
            # Define exact output
            mapped_path = os.path.join(UPLOAD_DIR, "input_mapped.json")
            out_records = df.to_dict(orient="records")
            
            with open(mapped_path, "w", encoding="utf-8") as f:
                json.dump(out_records, f, ensure_ascii=False, indent=2)
                
            return {"message": "Data saved successfully", "row_count": len(req.data)}
        except Exception as e:
            logger.error(f"Failed to save uploaded data: {e}")
            raise HTTPException(500, f"Failed to save data: {e}")

    # ── Pipeline Run Endpoint ──────────────────────────────────────────────────

    @app.post("/run-pipeline", tags=["Pipeline"])
    async def run_pipeline_endpoint(req: RunPipelineRequest):
        """Trigger the FAQ mining pipeline in a background thread."""
        with _pipeline_lock:
            if _pipeline_state["status"] == "running":
                raise HTTPException(status_code=409, detail="Pipeline is already running.")

        # Resolve input file
        input_file = req.input_file.strip() if req.input_file else ""
        if not input_file:
            # Try last uploaded file
            for ext in [".json", ".csv", ".xlsx", ".xls"]:
                candidate = os.path.join(UPLOAD_DIR, f"input{ext}")
                if os.path.isfile(candidate):
                    input_file = candidate
                    break
        if not input_file:
            input_file = DEFAULT_INPUT_FILE

        if not os.path.isfile(input_file):
            raise HTTPException(
                status_code=404,
                detail=f"Input file not found: '{input_file}'. Please upload a file first.",
            )

        thread = threading.Thread(target=_run_pipeline_thread, args=(input_file,), daemon=True)
        thread.start()
        return {"message": "Pipeline started.", "input_file": input_file}

    # ── Pipeline Status Endpoint ───────────────────────────────────────────────

    @app.get("/pipeline-status", tags=["Pipeline"])
    async def pipeline_status():
        """Poll pipeline progress. Returns status, current stage, and recent log lines."""
        with _pipeline_lock:
            snap = dict(_pipeline_state)
        snap["logs"] = snap["logs"][-50:]  # Return last 50 log lines
        elapsed = None
        if snap["started_at"]:
            end = snap["finished_at"] or time.time()
            elapsed = round(end - snap["started_at"], 1)
        snap["elapsed_seconds"] = elapsed
        return snap

    # ── FAQ Endpoints ─────────────────────────────────────────────────────────

    @app.get("/faqs", tags=["FAQs"])
    async def get_faqs(limit: int = Query(default=100, ge=1, le=10000)):
        _require_index()
        return {"faqs": _faqs[:limit], "total": len(_faqs)}

    @app.post("/search", tags=["Search"])
    async def search_faqs(req: SearchRequest) -> dict[str, Any]:
        _require_index()
        results = _faq_index.search(req.query, top_k=req.top_k)
        return {"query": req.query, "results": results, "count": len(results)}

    @app.post("/similar_questions", tags=["Search"])
    async def similar_questions(req: SimilarQuestionsRequest) -> dict[str, Any]:
        _require_index()
        if len(_valid_questions) == 0 or _valid_embeddings.shape[0] == 0:
            return {"query": req.query, "results": [], "count": 0}
        results = _faq_index.search_similar_questions(
            req.query, _valid_questions, _valid_embeddings, top_k=req.top_k
        )
        return {"query": req.query, "results": results, "count": len(results)}

    @app.get("/clusters", tags=["Analytics"])
    async def get_clusters() -> dict[str, Any]:
        _require_index()
        if not _analytics_report:
            raise HTTPException(503, "Analytics report not available. Run the pipeline first.")
        return {
            "clusters": _analytics_report.get("cluster_sizes", []),
            "total_clusters": len(_analytics_report.get("cluster_sizes", [])),
        }

    @app.get("/analytics", tags=["Analytics"])
    async def get_analytics() -> dict[str, Any]:
        _require_index()
        if not _analytics_report:
            raise HTTPException(503, "Analytics report not available. Run the pipeline first.")
        return _analytics_report

    @app.get("/visualization-data", tags=["Visualization"])
    async def get_visualization_data() -> dict[str, Any]:
        """
        Return 3D PCA projection of FAQ question embeddings for interactive
        cluster visualization. Supports Thai and English questions.
        Encoded with Sentence Transformers, projected via numpy SVD PCA.
        """
        if not _faqs:
            raise HTTPException(404, "No FAQs available. Run the pipeline first.")

        try:
            from backend.embedding_service import encode_texts, l2_normalize

            questions  = [f.get("faq_question", "") for f in _faqs]
            raw_embs   = encode_texts(questions)       # (n, D)
            norm_embs  = l2_normalize(raw_embs)        # L2-normalise for cosine
            coords_3d  = _pca_3d(norm_embs)            # (n, 3) via SVD PCA

            points = [
                {
                    "x":             float(coords_3d[i, 0]),
                    "y":             float(coords_3d[i, 1]),
                    "z":             float(coords_3d[i, 2]),
                    "cluster_id":    faq.get("cluster_id", 0),
                    "faq_question":  faq.get("faq_question", ""),
                    "faq_answer":    faq.get("faq_answer", ""),
                    "support_count": faq.get("support_count", 0),
                }
                for i, faq in enumerate(_faqs)
            ]

            return {"points": points, "count": len(points)}

        except Exception as exc:
            logger.error(f"Visualization data error: {exc}")
            raise HTTPException(500, f"Could not generate visualization: {exc}")

    @app.post("/faqs/relabel", tags=["FAQs"])
    async def relabel_faqs(req: RelabelFAQsRequest) -> dict[str, Any]:
        """
        Manually assign selected FAQs to a different cluster_id.
        Updates in-memory state, saves to disk, and rebuilds FAISS index.
        """
        global _faqs, _faq_index

        if not _faqs:
            raise HTTPException(404, "No FAQs loaded.")

        # Validate indices
        invalid = [i for i in req.indices if i < 0 or i >= len(_faqs)]
        if invalid:
            raise HTTPException(400, f"Invalid indices: {invalid}")

        try:
            # Update cluster_ids
            for i in req.indices:
                _faqs[i]["cluster_id"] = req.new_cluster_id

            # Persist to disk
            os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
            with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(_faqs, f, ensure_ascii=False, indent=2)

            # Rebuild FAISS index
            from backend.search_index import FAQSearchIndex
            new_index = FAQSearchIndex()
            new_index.build(_faqs)
            new_index.save()
            _faq_index = new_index

            logger.info(f"Relabeled {len(req.indices)} FAQ(s) to group {req.new_cluster_id}.")
            return {
                "relabeled": len(req.indices),
                "new_cluster_id": req.new_cluster_id,
                "message": f"{len(req.indices)} FAQ(s) successfully moved to group {req.new_cluster_id}.",
            }
        except Exception as e:
            logger.error(f"Relabel failed: {e}")
            raise HTTPException(500, f"Failed to save and rebuild FAISS index: {str(e)}")

    @app.post("/faqs/delete", tags=["FAQs"])
    async def delete_faqs(req: DeleteFAQsRequest) -> dict[str, Any]:
        """
        Delete FAQs by index. Persists the updated list to disk.
        Supports Thai and English text (utf-8 encoded).
        """
        global _faqs, _faq_index

        if not _faqs:
            raise HTTPException(404, "No FAQs loaded.")

        # Validate indices
        invalid = [i for i in req.indices if i < 0 or i >= len(_faqs)]
        if invalid:
            raise HTTPException(400, f"Invalid indices: {invalid}")

        try:
            # Remove selected FAQs
            indices_set = set(req.indices)
            remaining = [faq for idx, faq in enumerate(_faqs) if idx not in indices_set]

            # Persist to disk (utf-8 for Thai + English)
            os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
            with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(remaining, f, ensure_ascii=False, indent=2)

            # Update in-memory state
            _faqs = remaining

            # Rebuild FAISS index if FAQs remain
            if remaining:
                from backend.search_index import FAQSearchIndex
                new_index = FAQSearchIndex()
                new_index.build(remaining)
                new_index.save()
                _faq_index = new_index
            else:
                _faq_index = None

            deleted_count = len(req.indices)
            logger.info(f"Deleted {deleted_count} FAQ(s). {len(remaining)} remaining.")
            return {
                "deleted": deleted_count,
                "remaining": len(remaining),
                "message": f"{deleted_count} FAQ(s) deleted successfully.",
            }
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            raise HTTPException(500, f"Failed to save and rebuild FAISS index: {str(e)}")

    # ── Serve Frontend Static Files (must be LAST) ────────────────────────────
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return app


app = create_app()
