# 🔍 Sentinel Search Engine

A production-grade, distributed semantic search engine built with a custom Cyberpunk UI. Crawls the web asynchronously, processes pages in a decoupled background queue, indexes with transformer-based machine learning embeddings, and ranks results using a hybrid scoring algorithm. 

## ✨ Features

- **Decoupled Architecture** — Separate services for crawling, processing, and searching, connected via Celery and Redis.
- **Cyberpunk Frontend** — A highly aesthetic, professional-grade UI with Neon Cyan motifs, dynamic layouts, and zero analytical clutter.
- **Async Web Crawler** — 15 concurrent workers with `aiohttp`, per-domain rate limiting, and S3-compatible HTML storage.
- **Background Processing** — Celery workers parse HTML, extract backlinks, and generate AI embeddings asynchronously.
- **Semantic Embeddings** — Pages indexed using `all-MiniLM-L6-v2` PyTorch sentence transformers (384-dim vectors).
- **Hybrid Ranking** — Combines vector similarity (50%), backlink authority (30%), and keyword overlap (20%).
- **Dual Search Mode** — Seamlessly switch between MongoDB Atlas `$vectorSearch` and a memory-efficient local NumPy cosine fallback.
- **Containerized for Cloud** — Fully dockerized with a `docker-compose` stack and optimized memory constraints for free-tier deployments (e.g., Render).

## 🏗️ Architecture

```text
┌────────────────────────────────────────────────────────────────┐
│                     SENTINEL DISTRIBUTED PIPELINE              │
│                                                                │
│  Seed URLs ──► Async Fetcher (aiohttp) ──►  AWS S3 / MinIO     │
│                         │                    (Raw HTML)        │
│                         ▼                                      │
│                  Upstash Redis                                 │
│               (Message Broker)                                 │
│                         │                                      │
│                         ▼                                      │
│                Celery Indexer Worker                           │
│        (PyTorch Embeddings + BS4 Parsing)                      │
│                         │                                      │
│                         ▼                                      │
│                   MongoDB Atlas                                │
│                (Vectors & Metadata)                            │
│                         ▲                                      │
│                         │                                      │
│              FastAPI Search Backend                            │
│                         ▲                                      │
│                         │                                      │
│              Sentinel Cyberpunk UI                             │
└────────────────────────────────────────────────────────────────┘
```

## 📋 Prerequisites

- **Docker & Docker Compose** (Recommended for local setup)
- **Python** 3.10+ (If running without Docker)
- **Redis Cloud** (e.g., Upstash) or local Redis
- **MongoDB Atlas** (Free tier M0)
- **AWS S3** or MinIO for object storage

## 🚀 Setup & Deployment

### 1. Configure Environment Variables
Create a `.env` file in the root directory and populate it with your cloud credentials:

```bash
# MongoDB Atlas
MONGO_URI=mongodb+srv://<user>:<password>@cluster0.xxxx.mongodb.net/
MONGO_DB_NAME=search_engine
USE_ATLAS_SEARCH=false # Set to true if Vector Search Index is configured

# Upstash Redis / Celery
REDIS_HOST=xxx.upstash.io
REDIS_PORT=3912
CELERY_BROKER_URL=rediss://default:<password>@xxx.upstash.io:3912

# AWS S3 / MinIO
MINIO_ENDPOINT=s3.amazonaws.com
MINIO_REGION=us-east-1
MINIO_ACCESS_KEY=<aws_access_key>
MINIO_SECRET_KEY=<aws_secret_key>
MINIO_BUCKET_NAME=sentinel-crawled-pages
MINIO_SECURE=true
```

### 2. Local Docker Setup

The easiest way to run the entire stack locally is using `docker-compose`:

```bash
docker-compose up --build
```
This spins up the FastAPI backend, Celery worker, and async crawler simultaneously. 
Visit `http://localhost:8000` to access the Sentinel UI.

### 3. Manual Local Setup (Without Docker)

If you prefer running the Python scripts manually:

```bash
# Install dependencies
pip install -r requirements.txt

# Start the Celery Worker (Terminal 1)
celery -A celery_app worker --loglevel=info --pool=solo

# Start the Async Crawler (Terminal 2)
python fetcher.py

# Start the FastAPI Server (Terminal 3)
python search_api.py
```

### 4. Render Deployment

The backend is heavily optimized to run on the Render free tier (512MB RAM constraints):
- The `sentence-transformers` model is pre-cached directly in the Docker image during the build phase (`Dockerfile`).
- Memory allocation is optimized (`MALLOC_ARENA_MAX=2`).
- PyTorch thread usage is limited to avoid CPU starvation.

To deploy on Render:
1. Create a new **Web Service**.
2. Connect your GitHub repository.
3. Set the Environment to **Docker**.
4. Add all environment variables from your `.env` file.
5. Deploy!

## ⚙️ Configuration Reference

Settings can be overridden in `.env` or in `config.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_PAGES` | `50` | Stop crawling after N pages |
| `MAX_CONCURRENT_REQUESTS` | `15` | Total concurrent crawler workers |
| `DEFAULT_CRAWL_DELAY` | `0.5` | Politeness delay between requests |
| `USE_ATLAS_SEARCH` | `false` | Enable Atlas `$vectorSearch` |

## 📁 Project Structure

```
search-engine/
├── frontend/           # Sentinel Cyberpunk UI assets (HTML/CSS/JS)
├── config.py           # Centralized configuration 
├── fetcher.py          # Async web crawler to download and queue HTML
├── indexer.py          # Celery worker to generate PyTorch embeddings
├── celery_app.py       # Celery application configuration
├── robots_manager.py   # robots.txt compliance manager
├── ranker.py           # Hybrid ranking engine 
├── search_api.py       # FastAPI search endpoint serving the frontend
├── docker-compose.yml  # Docker stack configuration
├── Dockerfile          # Memory-optimized container definition
└── requirements.txt    # Python dependencies
```
