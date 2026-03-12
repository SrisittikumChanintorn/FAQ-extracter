FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for compilation (e.g. fairseq, HDBSCAN)
RUN apt-get update && apt-get install -y \
    build-essential \
    libomp-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
# Using --no-cache-dir to keep the image small
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose port for FastAPI
EXPOSE 8000

# Run FastAPI server via main.py to align with local development
CMD ["python", "backend/main.py", "--serve", "--host", "0.0.0.0", "--port", "8000"]
