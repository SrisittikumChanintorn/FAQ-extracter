# FAQ Mining System — v3

> AI-powered FAQ extraction from Thai customer support conversations.  
> Uses **local LLM (Ollama)** to extract real Q&A pairs — no paid API, no internet required.

---

## ⚡ Quick Start (Complete Setup)

### Step 0 — Prerequisites Check

Open PowerShell and verify Python is installed:

```powershell
python --version   # Need 3.9 or higher
pip --version
```

If Python is missing → download from https://www.python.org/downloads/

---

### Step 1 — Install Ollama

**Option A (Recommended) — One command:**
```powershell
winget install Ollama.Ollama
```

**Option B — Download installer manually:**  
Go to: **https://ollama.com/download/OllamaSetup.exe**  
Run the `.exe` → Click Install → **Close and reopen PowerShell**

**Verify installation:**
```powershell
ollama --version
# Expected output: ollama version 0.x.x
```

> After installation, Ollama runs as a **background service automatically**.  
> You do NOT need to run `ollama serve` manually.

---

### Step 2 — Choose and Download a Language Model

Pick the model that fits your RAM:

| Model | Size | RAM Needed | Thai Quality | Recommendation |
|---|---|---|---|---|
| `scb10x/llama3.1-typhoon2-8b-instruct` | 5.0 GB | 8 GB+ | ✅ Best Thai | **Best overall — start here** |

**Download your chosen model** (example: typhoon):
```powershell
ollama pull scb10x/llama3.1-typhoon2-8b-instruct
```
> This takes 5–20 minutes depending on internet speed. Progress bar will appear.

**Verify the model downloaded:**
```powershell
ollama list
# Should show: scb10x/llama3.1-typhoon2-8b-instruct   ... (size) ... (date)
```

**Quick test — confirm Thai response:**
```powershell
ollama run scb10x/llama3.1-typhoon2-8b-instruct "Summarize in one sentence: the customer asks how to transfer money."
```
Expected: a short coherent response. If you see a sensible answer → ✅ ready.

---

### Step 3 — Install Python Dependencies

```powershell
cd C:\Users\User\Desktop\context_extract
pip install -r requirements.txt
```

> First run downloads `BAAI/bge-m3` embedding model (~2.2 GB, automatic, cached).

---

### Step 4 — Configure the Model Name

Tell the system which Ollama model to use.

**Check the exact model name from step 2:**
```powershell
ollama list
```
The name shown here (e.g. `scb10x/llama3.1-typhoon2-8b-instruct`) is what you set below.

**Option A — Set per session (PowerShell):**
```powershell
$env:LLM_MODEL = "scb10x/llama3.1-typhoon2-8b-instruct"
```

**Option B — Set permanently in `backend/config.py`:**
```python
# Line ~153 in config.py
TOPIC_NAMER_MODEL = "scb10x/llama3.1-typhoon2-8b-instruct"   # ← change this
```

---

### Step 5 — Run the Pipeline

```powershell
# From the project directory:
python backend/main.py --input data/conversations.json --serve
```

Watch the console. You'll see stages progress:
```
Stage 1: Loading dataset...      → X records
Stage 2: Cleaning text...
Stage 3: Filtering questions...
Stage 4: LLM batch extraction... → calls Ollama per micro-batch
Stage 5: Merging batches...      → dedupe + sum mention_count
Stage 6: Building search index...→ bge-m3 embeddings + FAISS
Stage 7: Save & analytics...
...
Pipeline complete! X groups, Y FAQ pairs

======================================================================
  FAQ MINING SYSTEM — Server Starting
======================================================================
  Status:  starting (after ready → 200 OK)
  Port:    8000
  URL:     http://0.0.0.0:8000
  ------------------------------------------------------------------
  ➜  Copy & open:  http://localhost:8000
  ➜  API Docs:     http://localhost:8000/docs
  ➜  Health:       http://localhost:8000/health  (expect 200 when ready)
======================================================================
```
Copy the **URL** and paste in your browser. Check **Health** for status code 200 when the server is ready.

---

### Step 6 — Open the UI

```
http://localhost:8000
```

---

## 🔄 Subsequent Runs (Already Set Up)

```powershell
$env:LLM_MODEL = "scb10x/llama3.1-typhoon2-8b-instruct"
python backend/main.py --input data/conversations.json --serve
```

The pipeline uses **LLM per batch** then **merge**; embedding is used only for merging similar questions and for search.

---

## How It Works (Pipeline Overview)

Optimized **LLM-centric** flow (no UMAP/HDBSCAN):

```
Conversations
  → Stage 1–3:  Load → Clean → Filter valid Q&A pairs
  → Stage 4:    Split data into n_splits; for each split, LLM reads micro-batches
                and extracts FAQs + assigns group (Thai category name) per batch
  → Stage 5:    Merge batch results in pairs: same group name → merge;
                same/similar question → dedupe and sum mention_count
  → Stage 6:    Build FAISS search index (from group questions)
  → Stage 7:    Save output + analytics
  → Output:     groups[{ group_name, faqs:[{ question, answer, mention_count }] }]
```
Group names are **LLM-generated** (e.g. "MT5 login issues"). `mention_count` = how often that FAQ was seen across batches.

---

## Output Example

```json
{
  "total_groups": 5,
  "groups": [
    {
      "group_name": "MT5 login issues",
      "total_faqs": 8,
      "faqs": [
        { "question": "I cannot log in to MT5. What should I do?", "answer": "Please reset your password and try again...", "mention_count": 3 },
        { "question": "How do I reset my MT5 password?", "answer": "Contact support to verify your account, then reset the password...", "mention_count": 1 }
      ]
    }
  ]
}
```

---

## Input File Format

Upload any file with customer questions and admin answers. Column names don't matter — the UI has a column mapping step.

**JSON:**
```json
[
  { "customer_message": "I cannot log in to MT5", "admin_reply": "Please reset your password and try again." }
]
```

**CSV:**
```csv
customer_message,admin_reply
"I cannot log in to MT5","Please reset your password and try again."
```

**Excel (.xlsx):** Any two columns, map them in the UI.

---

## Configuration Reference (`backend/config.py`)

### LLM Settings

| Setting | Default | Description |
|---|---|---|
| `TOPIC_NAMER_PROVIDER` | `"ollama"` | Change to `"mock"` to skip LLM (for testing only) |
| `TOPIC_NAMER_MODEL` | `"scb10x/typhoon..."` | **Change to match your `ollama list` output** |
| `TOPIC_NAMER_OLLAMA_URL` | `"http://localhost:11434/api/generate"` | Default Ollama endpoint |
| **Env** `OLLAMA_TIMEOUT` | `none` | **No timeout by default (recommended for CPU).** Set to seconds (e.g. `3600`) or `none` |
| **Env** `OLLAMA_RETRY_COUNT` | `3` | Retries per LLM call on failure |
| **Env** `OLLAMA_RETRY_DELAY_SEC` | `10` | Seconds to wait between retries |

### Batch & Merge (LLM-centric)

| Setting | Default | Description |
|---|---|---|
| `FAQ_N_SPLITS` | `3` | Number of data splits; each is processed by LLM then results are merged in pairs |
| `FAQ_EXTRACTION_BATCH_SIZE` | `5` | Conversations per LLM call (micro-batch) |
| `MERGE_QUESTION_SIMILARITY_THRESHOLD` | `0.88` | When merging: questions with cosine similarity ≥ this are merged (mention_count summed); best answer kept |
| `MERGE_GROUP_NAME_MIN_SIMILARITY` | `0.7` | When merging: groups with name similarity ≥ this are merged |
| `MERGE_GROUP_USE_EMBEDDING` | `True` | Match group names by embedding (more accurate for Thai); fallback to string similarity |

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `ollama: not recognized` | Ollama not installed or PowerShell not restarted | Close & reopen PowerShell after install |
| `"No FAQ pairs extracted"` | Ollama not running / model not pulled | Run `ollama list` to confirm model exists |
| Ollama running but no response | Model name wrong in config | Run `ollama list`, copy exact name to `LLM_MODEL` |
| Pipeline very slow | LLM on CPU takes time | Normal — no timeout by default; retries 3x on failure. Optionally set `OLLAMA_TIMEOUT=3600` if you want a limit |
| Timeout / connection error | Previously default 10800s could still fail on slow CPU | Default is now **no timeout** (`OLLAMA_TIMEOUT=none`). System retries automatically |
| Too few groups | Dataset too small | Lower thresholds in config or add more data |
| bge-m3 download slow | First run only | 2.2 GB download, cached after first run |

---

## Project Structure

```
context_extract/
├── backend/
│   ├── __init__.py            ← Package marker
│   ├── config.py              ← All settings (edit this)
│   ├── main.py                ← Pipeline orchestrator
│   ├── api.py                 ← FastAPI REST + frontend
│   ├── data_loader.py         ← Stage 1: Load files
│   ├── text_cleaner.py        ← Stage 2: Clean text
│   ├── question_filter.py     ← Stage 3: Filter Q&A
│   ├── batch_extractor.py     ← Stage 4: LLM batch extraction + groups
│   ├── batch_merger.py        ← Stage 5: Merge batches (embedding + best answer)
│   ├── embedding_service.py   ← bge-m3 embeddings (1024-dim)
│   ├── search_index.py        ← Stage 6: FAISS
│   └── analytics.py           ← Reports
├── frontend/
│   ├── index.html             ← Single-page app UI
│   ├── manual.html            ← User manual (Thai)
│   ├── app.js                 ← UI logic
│   ├── search_ui.js           ← Search interface logic
│   ├── viz.js                 ← 3D cluster visualization
│   ├── terms.html             ← Terms of service page
│   └── privacy.html           ← Privacy policy page
├── data/
│   ├── conversations.json     ← Sample dataset
│   ├── uploads/               ← Uploaded files (auto-created)
│   └── faqs.json              ← Output (auto-generated)
├── .gitignore
├── requirements.txt
├── generate_mock_data.py      ← Generate sample conversations
├── test_api.py                ← API integration tests
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## API Reference

Base URL: `http://localhost:8000` | Swagger: `/docs`

### System

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Liveness check (status, faq_count, index_ready, pipeline_status) |
| GET | `/manual` | Serve user manual page |

### Pipeline

| Method | Endpoint | Description |
|---|---|---|
| POST | `/upload` | Upload data file (xlsx / csv / json) |
| POST | `/preview-data` | Read uploaded file headers + top 5 sample rows for column mapping |
| POST | `/apply-mapping` | Map customer/admin columns, save as normalized JSON |
| POST | `/run-pipeline` | Trigger FAQ mining pipeline in background |
| GET | `/pipeline-status` | Poll pipeline progress (stage, logs, elapsed) |

### FAQs

| Method | Endpoint | Description |
|---|---|---|
| GET | `/groups` | **Primary** — all FAQ groups with Q&A lists |
| GET | `/faqs` | Legacy alias (same data, different envelope) |
| POST | `/faqs/edit` | Edit question/answer text of a specific FAQ |
| POST | `/faqs/relabel` | Move FAQ(s) to a different group |
| POST | `/faqs/delete` | Delete FAQ(s) by index |
| POST | `/faqs/merge-groups` | Merge source group into target group |

### Search

| Method | Endpoint | Description |
|---|---|---|
| POST | `/search` | Semantic FAQ search: `{"query": "...", "top_k": 5}` |
| POST | `/similar_questions` | Historical question similarity search |

### Data & Analytics

| Method | Endpoint | Description |
|---|---|---|
| GET | `/analytics` | Statistics report |
| GET | `/clusters` | Cluster metadata list |
| GET | `/visualization-data` | 3D PCA projection for interactive cluster visualization |
| GET | `/uploaded-data` | Fetch most recently uploaded raw data |
| POST | `/save-uploaded-data` | Save frontend-edited data back for pipeline |
| GET | `/export?fmt=json` | Export all FAQs as JSON (or `fmt=csv` for CSV) |

---

## Use Without UI (CLI)

```powershell
# Run pipeline + start server
$env:LLM_MODEL = "scb10x/llama3.1-typhoon2-8b-instruct"
python backend/main.py --input data/conversations.json --serve

# Pipeline only (no server)
python backend/main.py --input data/conversations.json

# Test mode — no Ollama needed (mock LLM)
$env:LLM_PROVIDER = "mock"
python backend/main.py --input data/conversations.json --serve
```

---

## Docker Deployment

### Option A — docker-compose (recommended)

```powershell
docker-compose up -d --build
```

> Edit `docker-compose.yml` to set `LLM_MODEL` and `OLLAMA_URL` if needed.

### Option B — manual docker run

```powershell
docker build -t faq-miner-ai .
docker run -d -p 8000:8000 -v faq_data:/app/data `
  -e LLM_MODEL=scb10x/llama3.1-typhoon2-8b-instruct `
  -e OLLAMA_URL=http://host.docker.internal:11434/api/generate `
  --name faq_miner faq-miner-ai
```

> Ollama must run on the **host machine**. Use `host.docker.internal` instead of `localhost`.
>
> The container starts the **API server only**. Open `http://localhost:8000`, upload your file, then click **Start Analysis** (or call `POST /run-pipeline`).
