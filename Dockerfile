FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Minimal runtime system deps (faiss / numpy wheels may rely on libgomp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose port for the web UI and API (status 200 at /health when ready)
EXPOSE 8000

# Start the API server. Run the pipeline from the UI (or POST /run-pipeline).
CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
