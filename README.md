# FAQ Mining System

> AI-powered FAQ extraction from customer support conversations.
> Uses sentence transformers, HDBSCAN clustering, and FAISS semantic search.
> Supports Thai and English text.

---

## Quick Start (3 Steps)

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note:** First run downloads the `all-MiniLM-L6-v2` model (~80 MB, automatic).

### 2. Start the Server

```bash
python backend/main.py --serve
```

### 3. Open in Browser

```
http://localhost:8000
```

That's it. **No command line needed after this.** Everything is done through the UI.

---

## Deployment (Docker)

To deploy the application in a production environment, use Docker.

### Option 1: Docker CLI

```bash
docker build -t faq-miner-ai .
docker run -d -p 8000:8000 -v faq_data:/app/data --name faq_miner faq-miner-ai
```

### Option 2: Docker Compose

```bash
docker-compose up -d
```

The application will be accessible at `http://localhost:8000`.

---

## How to Use the UI

| Step | Tab                             | Action                                                                                               |
| ---- | ------------------------------- | ---------------------------------------------------------------------------------------------------- |
| 1    | 📂 **Upload Data**         | Upload Excel/CSV/JSON. A **Data Mapping** UI will appear to select your Question/Answer columns |
| 2    | ⚙️ **Process & Analyze** | Adjust settings, then click **▶ Start Analysis**                                               |
| 3    | ❓ **FAQ Library**         | View extracted FAQs, filter by Topic Group                                                           |
| 4    | 🔍 **Smart Search**        | Type any question — AI finds the best match                                                         |
| 5    | 🔗 **Topic Groups**        | Browse question clusters found by AI                                                                 |
| 6    | 📊 **Reports & Charts**    | View statistics, click **Refresh Charts**                                                       |
| 7    | 🗂️ **Data Management**   | Relabel groups or delete bad FAQs to refine the AI output (Supports Individual and Group Views) |
| 8    | 📖 **Support**             | Read the embedded User Manual, Terms, and Privacy Policy without leaving the system             |

---

## Input File Format

All formats require these two columns:

| Column Name          | Description                               |
| -------------------- | ----------------------------------------- |
| `customer_message` | The customer's question (Thai or English) |
| `admin_reply`      | The support agent's answer                |

**JSON example:**

```json
[
  { "customer_message": "สินค้ายังไม่ได้รับเลย", "admin_reply": "กรุณาติดต่อ 02-xxx-xxxx" },
  { "customer_message": "How do I track my order?", "admin_reply": "Visit the Track Order page." }
]
```

**CSV example:**

```csv
customer_message,admin_reply
"สินค้ายังไม่ได้รับ","กรุณาติดต่อ 02-xxx-xxxx"
"How do I return?","Returns accepted within 30 days."
```

> **Thai & English:** Both languages are fully supported. All data is stored as UTF-8.

---

## Configuration

Edit `backend/config.py` to tune the system:

| Setting                        | Default              | Description                                  |
| ------------------------------ | -------------------- | -------------------------------------------- |
| `DEDUP_SIMILARITY_THRESHOLD` | `0.92`             | Strictness of duplicate removal (0.70–0.99) |
| `CLUSTER_MIN_CLUSTER_SIZE`   | `3`                | Minimum questions per topic group            |
| `CLUSTER_MIN_SIZE_THRESHOLD` | `2`                | Discard groups smaller than this             |
| `EMBEDDING_MODEL_NAME`       | `all-MiniLM-L6-v2` | AI embedding model                           |
| `FAISS_TOP_K_DEFAULT`        | `5`                | Default search results count                 |

> **Tip for small datasets:** Lower `CLUSTER_MIN_CLUSTER_SIZE` to `2` to generate more FAQs.

---

## Project Structure

```
context_extract/
├── backend/
│   ├── config.py            ← All settings (edit this)
│   ├── main.py              ← Pipeline orchestrator (CLI mode)
│   ├── api.py               ← FastAPI: REST API + frontend serving
│   ├── data_loader.py       ← Step 1: Load JSON / CSV / Excel
│   ├── text_cleaner.py      ← Step 2: Clean text
│   ├── question_filter.py   ← Step 3: Filter valid questions
│   ├── embedding_service.py ← Step 4: AI sentence embeddings
│   ├── deduplication.py     ← Step 5: Remove duplicates
│   ├── clustering.py        ← Step 6+7: HDBSCAN grouping
│   ├── faq_generator.py     ← Step 8-10: Generate FAQ answers
│   ├── search_index.py      ← Step 11: FAISS search index
│   └── analytics.py         ← Step 13: Reports
├── frontend/
│   ├── index.html           ← Single-page UI (7 tabs)
│   └── app.js               ← All UI logic
├── data/
│   ├── conversations.json   ← Sample dataset
│   ├── uploads/             ← Uploaded files
│   ├── faqs.json            ← Extracted FAQs (auto-generated)
│   └── analytics_report.json
└── requirements.txt
```

---

## API Reference

Base URL: `http://localhost:8000`

| Method | Endpoint                | Description                            |
| ------ | ----------------------- | -------------------------------------- |
| GET    | `/health`             | Server status check                    |
| GET    | `/manual`             | Serves the User Manual HTML            |
| POST   | `/upload`             | Upload file (multipart/form-data)      |
| POST   | `/preview-data`       | Read uploaded headers and rows         |
| POST   | `/apply-mapping`      | Rename columns and save for processing |
| POST   | `/run-pipeline`       | Start AI analysis in background        |
| GET    | `/pipeline-status`    | Poll analysis progress                 |
| GET    | `/faqs`               | Get all FAQs                           |
| POST   | `/search`             | Semantic FAQ search                    |
| GET    | `/clusters`           | Get topic groups                       |
| GET    | `/analytics`          | Get full report                        |
| GET    | `/visualization-data` | Get 3D cluster points                  |
| POST   | `/faqs/relabel`       | Manually change the FAQ's topic group  |
| POST   | `/faqs/delete`        | Delete FAQs by index                   |

---

## Performance

| Dataset Size    | Pipeline Time | Search Latency |
| --------------- | ------------- | -------------- |
| 1,000 records   | ~15 seconds   | < 10 ms        |
| 10,000 records  | ~90 seconds   | < 50 ms        |
| 100,000 records | ~15 minutes   | < 200 ms       |

> Embeddings are cached to `data/embeddings_cache.npy` — re-runs skip Step 4 automatically.

---

## CLI (for developers)

```bash
# Run pipeline + start server in one command
python backend/main.py --serve

# Run pipeline only (generates data files, no server)
python backend/main.py --input data/conversations.json
```
