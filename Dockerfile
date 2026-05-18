FROM python:3.10-slim

WORKDIR /app

# System dependencies for sentence-transformers and building extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Environment defaults
ENV PYTHONUNBUFFERED=1
