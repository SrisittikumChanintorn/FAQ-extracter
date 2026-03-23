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

# ── Helpers (data normalization) ──────────────────────────────────────────────

def _normalize_question_key(text: str) -> str:
    """Normalize a question to a stable key for dedup (case-fold, collapse whitespace, strip edge punct)."""
    import re as _re
    import unicodedata as _ud
    s = _ud.normalize("NFKC", (text or ""))
    s = _re.sub(r"\s+", " ", s).strip()
    s = _re.sub(r"^[\s\W_]+|[\s\W_]+$", "", s, flags=_re.UNICODE).strip()
    return s.casefold()


def _dedup_faqs_in_group(faqs: list[dict]) -> list[dict]:
    """Collapse FAQ items whose questions differ only in case / whitespace."""
    if len(faqs) <= 1:
        return faqs
    seen: dict[str, int] = {}
    deduped: list[dict] = []
    for faq in faqs:
        if not isinstance(faq, dict):
            continue
        key = _normalize_question_key(faq.get("question", ""))
        if not key:
            deduped.append(faq)
            continue
        if key in seen:
            existing = deduped[seen[key]]
            cnt = int(faq.get("mention_count", 1) or 1)
            existing["mention_count"] = int(existing.get("mention_count", 1) or 1) + cnt
            if len((faq.get("answer") or "").strip()) > len((existing.get("answer") or "").strip()):
                existing["answer"] = faq["answer"]
        else:
            seen[key] = len(deduped)
            deduped.append(faq)
    return deduped


def _normalize_groups_in_place(groups: list[dict]) -> list[dict]:
    """
    Ensure groups have stable, unique group_id/cluster_id and expected fields.
    Also deduplicates FAQs within each group that differ only in case/whitespace.
    """
    if not groups:
        return []
    for i, g in enumerate(groups):
        if not isinstance(g, dict):
            continue
        g["group_id"] = i
        g["cluster_id"] = i
        g["group_name"] = (g.get("group_name") or "").strip() or "Other"
        faqs = g.get("faqs", [])
        if not isinstance(faqs, list):
            faqs = []
        faqs = _dedup_faqs_in_group(faqs)
        g["faqs"] = faqs
        g["total_faqs"] = len(faqs)
        g["total_questions"] = len(faqs)
        if "support_count" not in g or g.get("support_count") is None:
            g["support_count"] = sum(int(f.get("mention_count", 1) or 1) for f in faqs if isinstance(f, dict))
    return groups

# ── Pipeline State (thread-safe progress tracking) ────────────────────────────

_pipeline_lock = threading.Lock()
_pipeline_state = {
    "status": "idle",          # idle | running | done | error
    "stage": 0,
    "total_stages": 7,
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
    _faqs = _normalize_groups_in_place(list(faqs or []))
    _analytics_report = analytics
    _valid_questions = valid_questions
    _valid_embeddings = valid_embeddings
    logger.info(f"API state updated: {len(_faqs)} groups, {len(_valid_questions)} historical questions.")


# ── Pipeline Runner (runs in a background thread) ─────────────────────────────

def _run_pipeline_thread(input_file: str, n_splits: int = None, batch_size: int = None):
    global _faq_index, _faqs, _analytics_report, _valid_questions, _valid_embeddings

    def progress_cb(stage: int, stage_name: str, message: str):
        _log(message, stage=stage, stage_name=stage_name)

    try:
        _reset_pipeline_state(input_file)
        from backend.main import run_pipeline
        state = run_pipeline(input_file, progress_callback=progress_cb, n_splits_override=n_splits, batch_size_override=batch_size)
        set_pipeline_state(
            faq_index=state["faq_index"],
            faqs=state["groups"],
            analytics=state["analytics"],
            valid_questions=state["valid_questions"],
            valid_embeddings=state["valid_embeddings"],
        )
        n_groups = len(state["groups"])
        n_items = sum(g.get("total_faqs", 0) for g in state["groups"])
        _log(f"✅ Pipeline complete! {n_groups} groups, {n_items} FAQ items.")
        with _pipeline_lock:
            _pipeline_state["status"] = "done"
            _pipeline_state["faq_count"] = n_groups
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

def _load_groups_from_disk() -> list:
    """Load groups from FAQ_OUTPUT_FILE. Supports both {groups: []} and plain list."""
    if not os.path.isfile(FAQ_OUTPUT_FILE):
        return []
    try:
        with open(FAQ_OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "groups" in data:
            return _normalize_groups_in_place(list(data["groups"] or []))
        if isinstance(data, list):
            return _normalize_groups_in_place(list(data))
        return []
    except Exception as e:
        logger.warning(f"Could not load {FAQ_OUTPUT_FILE}: {e}")
        return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: prepare directories but do NOT auto-load saved data.
    The UI starts empty; the user must explicitly load mockup or upload data."""
    global _faq_index, _faqs, _analytics_report

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    logger.info("API ready (clean start). Upload data or load mockup from the UI.")

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

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    class NoCacheHTMLMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            response = await call_next(request)
            ct = response.headers.get("content-type", "")
            if "text/html" in ct or request.url.path.endswith((".js", ".css")):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response

    app.add_middleware(NoCacheHTMLMiddleware)

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
        n_splits: int = Field(
            default=0,
            description="Number of data splits for batch extraction. 0 = auto-calculate based on row count.",
        )
        batch_size: int = Field(
            default=0,
            description="Rows per micro-batch for LLM extraction. 0 = auto-calculate based on row count.",
        )

    class DeleteFAQsRequest(BaseModel):
        indices: list[int] = Field(..., description="List of FAQ indices (0-based) to delete.")

    class RelabelFAQsRequest(BaseModel):
        indices: list[int] = Field(..., description="List of FAQ indices (0-based) to relabel.")
        new_cluster_id: int = Field(..., description="The ID of the new cluster/group.")

    class EditFAQRequest(BaseModel):
        index: int = Field(..., description="0-based index of the FAQ to edit.")
        question: str = Field(None, description="New question text.")
        answer: str = Field(None, description="New answer text.")

    class MergeGroupsRequest(BaseModel):
        source_group_id: int = Field(..., description="ID of the group to be merged.")
        target_group_id: int = Field(..., description="ID of the group to merge into.")

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
        uploaded_candidates = [
            os.path.join(UPLOAD_DIR, "input_mapped.json"),
            os.path.join(UPLOAD_DIR, "input.json"),
            os.path.join(UPLOAD_DIR, "input.csv"),
            os.path.join(UPLOAD_DIR, "input.xlsx"),
            os.path.join(UPLOAD_DIR, "input.xls"),
        ]
        
        target_file = None
        is_sample = False
        for cand in uploaded_candidates:
            if os.path.isfile(cand):
                target_file = cand
                break
        
        if not target_file:
            if os.path.isfile(DEFAULT_INPUT_FILE):
                target_file = DEFAULT_INPUT_FILE
                is_sample = True
            else:
                raise HTTPException(404, "No uploaded data found.")
            
        try:
            from backend.data_loader import load_support_data
            df = load_support_data(target_file, validate=False)
            df.fillna("", inplace=True)
            return {"data": df.to_dict(orient="records"), "is_sample": is_sample}
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

        # Resolve input file (prefer mapped data from UI)
        input_file = req.input_file.strip() if req.input_file else ""
        if not input_file:
            mapped_path = os.path.join(UPLOAD_DIR, "input_mapped.json")
            if os.path.isfile(mapped_path):
                input_file = mapped_path
            else:
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
                detail=f"Input file not found: '{input_file}'. Please upload a file or load mockup data first.",
            )

        # Pass dynamic batch params (0 means auto)
        n_splits = req.n_splits if req.n_splits > 0 else None
        batch_size = req.batch_size if req.batch_size > 0 else None

        thread = threading.Thread(
            target=_run_pipeline_thread,
            args=(input_file,),
            kwargs={"n_splits": n_splits, "batch_size": batch_size},
            daemon=True,
        )
        thread.start()
        return {"message": "Pipeline started.", "input_file": input_file, "n_splits": n_splits, "batch_size": batch_size}

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

    # ── FAQ / Group Endpoints ─────────────────────────────────────────────────

    @app.get("/groups", tags=["FAQs"])
    async def get_groups(limit: int = Query(default=100, ge=1, le=10000)):
        """
        Return FAQ groups in the canonical new schema.
        Returns empty list when no pipeline has been run (no 503).
        """
        groups = (_faq_index.groups[:limit] if _faq_index and _faq_index.is_ready else _faqs[:limit]) if _faqs else []
        return {"total_groups": len(_faqs) if _faqs else 0, "groups": groups}

    @app.get("/faqs", tags=["FAQs"])
    async def get_faqs(limit: int = Query(default=100, ge=1, le=10000)):
        """Backwards-compatible FAQ list. Returns empty when no data (no 503)."""
        data = (_faqs[:limit] if _faqs else [])
        return {"faqs": data, "total": len(_faqs) if _faqs else 0}

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
        """Return cluster list for UI. When no analytics, derive from current groups (no 503)."""
        if _analytics_report and _analytics_report.get("cluster_sizes"):
            clusters = _analytics_report["cluster_sizes"]
        elif _faqs:
            clusters = [
                {
                    "cluster_id": g.get("cluster_id", g.get("group_id", i)),
                    "group_name": g.get("group_name", "Other"),
                    "size": g.get("total_faqs", len(g.get("faqs", []))),
                    "support_count": g.get("support_count", 0),
                }
                for i, g in enumerate(_faqs)
            ]
        else:
            clusters = []
        return {"clusters": clusters, "total_clusters": len(clusters)}

    @app.get("/analytics", tags=["Analytics"])
    async def get_analytics() -> dict[str, Any]:
        """Return analytics report. Empty structure when none (no 503)."""
        if not _analytics_report:
            return {
                "summary": {},
                "top_faq_topics": [],
                "cluster_sizes": [],
                "unanswered_noise_questions": [],
            }
        return _analytics_report

    @app.get("/visualization-data", tags=["Visualization"])
    async def get_visualization_data() -> dict[str, Any]:
        """
        Return 3D PCA projection for interactive visualization:
        - Each point represents an extracted FAQ item (question).
        - Points are colored/filtered by their parent group (cluster_id).
        """
        if not _faqs:
            raise HTTPException(404, "No FAQs available. Run the pipeline first.")

        try:
            from backend.embedding_service import encode_texts, l2_normalize

            rows: list[dict[str, Any]] = []
            texts: list[str] = []

            for g in _faqs:
                gid = g.get("cluster_id", g.get("group_id", 0))
                gname = g.get("group_name") or "Other"
                for f in (g.get("faqs") or []):
                    if not isinstance(f, dict):
                        continue
                    q = (f.get("question") or "").strip()
                    a = (f.get("answer") or "").strip()
                    if not q:
                        continue
                    rows.append(
                        {
                            "cluster_id": gid,
                            "group_name": gname,
                            "faq_question": q,
                            "faq_answer": a,
                            "mention_count": int(f.get("mention_count", 1) or 1),
                        }
                    )
                    texts.append(q)

            if len(texts) < 2:
                return {"points": [], "count": 0}

            raw_embs = encode_texts(texts, is_query=False)
            norm_embs = l2_normalize(raw_embs)
            coords_3d = _pca_3d(norm_embs)

            points = []
            for i, r in enumerate(rows):
                points.append(
                    {
                        "x": float(coords_3d[i, 0]),
                        "y": float(coords_3d[i, 1]),
                        "z": float(coords_3d[i, 2]),
                        **r,
                    }
                )

            return {"points": points, "count": len(points), "total_groups": len(_faqs)}

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

            # Persist to disk (canonical format)
            os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
            with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump({"total_groups": len(_faqs), "groups": _faqs}, f, ensure_ascii=False, indent=2)

            # Rebuild FAISS index
            from backend.search_index import FAQSearchIndex
            new_index = FAQSearchIndex()
            new_index.build(_faqs)
            new_index.save()
            _faq_index = new_index

            logger.info(f"Relabeled {len(req.indices)} group(s) to cluster {req.new_cluster_id}.")
            return {
                "relabeled": len(req.indices),
                "new_cluster_id": req.new_cluster_id,
                "message": f"{len(req.indices)} FAQ(s) successfully moved to group {req.new_cluster_id}.",
            }
        except Exception as e:
            logger.error(f"Relabel failed: {e}")
            raise HTTPException(500, f"Failed to save and rebuild FAISS index: {str(e)}")

    @app.post("/faqs/edit", tags=["FAQs"])
    async def edit_faq(req: EditFAQRequest) -> dict[str, Any]:
        """Edit the question or answer text of a specific FAQ."""
        global _faqs, _faq_index

        if not _faqs:
            raise HTTPException(404, "No FAQs loaded.")
        if req.index < 0 or req.index >= len(_faqs):
            raise HTTPException(400, f"Invalid index: {req.index}")

        try:
            faq = _faqs[req.index]
            if req.question is not None:
                faq["faq_question"] = req.question
                faq["canonical_question"] = req.question
            if req.answer is not None:
                faq["faq_answer"] = req.answer
                faq["canonical_answer"] = req.answer
                faq["suggested_admin_reply"] = req.answer

            # Persist to disk (canonical format)
            os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
            with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump({"total_groups": len(_faqs), "groups": _faqs}, f, ensure_ascii=False, indent=2)

            # Rebuild FAISS index
            from backend.search_index import FAQSearchIndex
            new_index = FAQSearchIndex()
            new_index.build(_faqs)
            new_index.save()
            _faq_index = new_index

            return {
                "message": f"FAQ {req.index} updated successfully.",
                "faq": faq
            }
        except Exception as e:
            logger.error(f"Edit failed: {e}")
            raise HTTPException(500, f"Failed to save and rebuild index: {str(e)}")

    @app.post("/faqs/merge-groups", tags=["FAQs"])
    async def merge_groups(req: MergeGroupsRequest) -> dict[str, Any]:
        """Merge source group into target: append source's faqs to target, then remove source group."""
        global _faqs, _faq_index

        if not _faqs:
            raise HTTPException(404, "No FAQs loaded.")

        try:
            src_idx = None
            tgt_idx = None
            for i, g in enumerate(_faqs):
                cid = g.get("cluster_id", g.get("group_id", i))
                if cid == req.source_group_id:
                    src_idx = i
                if cid == req.target_group_id:
                    tgt_idx = i
            if src_idx is None:
                raise HTTPException(404, f"Source group {req.source_group_id} not found.")
            if tgt_idx is None:
                raise HTTPException(404, f"Target group {req.target_group_id} not found.")
            if src_idx == tgt_idx:
                raise HTTPException(400, "Source and target must be different.")

            src_group = _faqs[src_idx]
            tgt_group = _faqs[tgt_idx]
            src_faqs = list(src_group.get("faqs", []))
            tgt_faqs = list(tgt_group.get("faqs", []))
            tgt_faqs.extend(src_faqs)
            tgt_group["faqs"] = tgt_faqs
            tgt_group["total_faqs"] = len(tgt_faqs)
            tgt_group["support_count"] = tgt_group.get("support_count", 0) + src_group.get("support_count", 0)
            tgt_group["representative_questions"] = [f.get("question", "") for f in tgt_faqs[:5] if f.get("question")]
            if not tgt_group["representative_questions"]:
                tgt_group["representative_questions"] = [tgt_group.get("group_name", "Other")]
            tgt_group["canonical_question"] = tgt_faqs[0].get("question", "") if tgt_faqs else ""
            tgt_group["faq_question"] = tgt_group["canonical_question"]
            tgt_group["canonical_answer"] = tgt_faqs[0].get("answer", "") if tgt_faqs else ""
            tgt_group["faq_answer"] = tgt_group["canonical_answer"]
            tgt_group["suggested_admin_reply"] = tgt_group["canonical_answer"]

            remaining = [_faqs[i] for i in range(len(_faqs)) if i != src_idx]
            for i, g in enumerate(remaining):
                g["group_id"] = i
                g["cluster_id"] = i
            _faqs = remaining

            os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
            with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump({"total_groups": len(_faqs), "groups": _faqs}, f, ensure_ascii=False, indent=2)

            from backend.search_index import FAQSearchIndex
            new_index = FAQSearchIndex()
            new_index.build(_faqs)
            new_index.save()
            _faq_index = new_index

            logger.info(f"Merged group {req.source_group_id} into {req.target_group_id}. {len(src_faqs)} FAQs moved.")
            return {
                "merged_count": len(src_faqs),
                "message": f"Successfully merged {len(src_faqs)} FAQs into target group.",
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Merge failed: {e}")
            raise HTTPException(500, f"Failed to save and rebuild index: {str(e)}")

    @app.get("/export", tags=["Data"])
    async def export_data(fmt: str = Query("json", description="Export format: json or csv")):
        """Export all FAQs as JSON or CSV."""
        if not _faqs:
            raise HTTPException(404, "No FAQs loaded.")
        
        if fmt.lower() == "csv":
            import io
            import csv
            from fastapi.responses import StreamingResponse
            
            output = io.StringIO()
            writer = csv.writer(output)
            # Write header
            writer.writerow(["group_id", "group_name", "canonical_question", "canonical_answer", "confidence_score", "support_count"])
            for f in _faqs:
                writer.writerow([
                    f.get("cluster_id", f.get("group_id", "")),
                    f.get("group_name", ""),
                    f.get("canonical_question", f.get("faq_question", "")),
                    f.get("canonical_answer", f.get("faq_answer", "")),
                    f.get("confidence_score", 1.0),
                    f.get("support_count", 1)
                ])
                
            output.seek(0)
            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=faqs_export.csv"}
            )
        
        # Default JSON
        return JSONResponse(
            content={"total": len(_faqs), "faqs": _faqs},
            headers={"Content-Disposition": "attachment; filename=faqs_export.json"}
        )

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

            # Persist to disk (canonical format)
            os.makedirs(os.path.dirname(FAQ_OUTPUT_FILE), exist_ok=True)
            with open(FAQ_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump({"total_groups": len(remaining), "groups": remaining}, f, ensure_ascii=False, indent=2)

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

    # ── Load Mockup Data Endpoint ─────────────────────────────────────────────

    @app.post("/load-mockup", tags=["Pipeline"])
    async def load_mockup_data():
        """Import the built-in mockup dataset into uploads (no analysis)."""
        if not os.path.isfile(DEFAULT_INPUT_FILE):
            # Generate mock data on the fly if it doesn't exist
            try:
                import subprocess as _sp
                gen_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generate_mock_data.py")
                if os.path.isfile(gen_script):
                    _sp.run([sys.executable, gen_script], check=True)
                    logger.info("Generated mock data via generate_mock_data.py")
                else:
                    raise FileNotFoundError("generate_mock_data.py not found")
            except Exception as e:
                raise HTTPException(500, f"Could not generate mock data: {e}")

        if not os.path.isfile(DEFAULT_INPUT_FILE):
            raise HTTPException(404, "Default input file (conversations.json) not found.")

        try:
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            dest = os.path.join(UPLOAD_DIR, "input_mapped.json")
            shutil.copyfile(DEFAULT_INPUT_FILE, dest)
            # Count rows
            with open(dest, "r", encoding="utf-8") as f:
                data = json.load(f)
            row_count = len(data) if isinstance(data, list) else 0
            logger.info(f"Mockup data loaded: {row_count} rows → {dest}")
            return {
                "message": "Mockup data loaded successfully. Ready for processing.",
                "row_count": row_count,
                "file_path": dest,
            }
        except Exception as e:
            logger.error(f"Failed to load mockup data: {e}")
            raise HTTPException(500, f"Failed to load mockup data: {e}")

    # ── Serve Frontend Static Files (must be LAST) ────────────────────────────
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return app


app = create_app()
