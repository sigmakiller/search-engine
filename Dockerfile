FROM python:3.10-slim

WORKDIR /app

# System dependencies for sentence-transformers and building extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy source code
COPY . .

# Pre-download the embedding model to bake it into the Docker image and avoid memory spikes on boot
ENV HF_HOME=/app/hf_cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Environment defaults
ENV PYTHONUNBUFFERED=1
ENV MALLOC_ARENA_MAX=2

# Start the application using Render's PORT, defaulting to 8000
CMD ["sh", "-c", "uvicorn search_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
