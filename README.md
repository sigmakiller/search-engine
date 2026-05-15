# 🔍 MiniSearch Engine

A production-grade semantic search engine built from scratch in Python. Crawls the web using async BFS, indexes pages with transformer-based embeddings, and ranks results using a hybrid scoring algorithm combining vector similarity, backlink authority, and keyword matching.

## ✨ Features

- **Async Web Crawler** — 10 concurrent workers with `aiohttp`, per-domain rate limiting, and retry with exponential backoff
- **robots.txt Compliant** — RFC 9309-aware parsing via Protego with per-domain caching and crawl-delay support
- **Semantic Embeddings** — Pages indexed using `all-MiniLM-L6-v2` sentence transformer (384-dim vectors)
- **Full-Text Indexing** — Embeds title + meta description + body text for richer search results
- **Hybrid Ranking** — Combines vector similarity (50%), backlink authority (30%), and keyword overlap (20%)
- **URL Deduplication** — Normalizes URLs to prevent duplicate crawling (strips fragments, sorts params, etc.)
- **FastAPI Search API** — RESTful endpoints with pagination, stats, and auto-generated Swagger docs
- **Dual Search Mode** — Supports MongoDB Atlas `$vectorSearch` and local NumPy cosine similarity fallback

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     CRAWL PIPELINE                       │
│                                                          │
│  Seed URLs ──► asyncio.Queue ──► Worker Pool (10x)       │
│                                    │                     │
│                    ┌───────────────┤                     │
│                    ▼               ▼                     │
│              robots.txt      Domain Rate                 │
│              Manager         Limiter                     │
│                    │               │                     │
│                    └───────┬───────┘                     │
│                            ▼                             │
│                    Fetch + Parse + Embed                  │
│                            │                             │
│                    ┌───────┴───────┐                     │
│                    ▼               ▼                     │
│               MongoDB          Redis                     │
│            (pages, vectors)  (queue, backlinks,          │
│                               visited set)               │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                     SEARCH PIPELINE                      │
│                                                          │
│  Query ──► Encode ──► Vector Search ──► Hybrid Ranker    │
│                       (Atlas or NumPy)       │           │
│                                              ▼           │
│                                     ┌────────────────┐   │
│                                     │ vector sim 50% │   │
│                                     │ backlinks  30% │   │
│                                     │ text match 20% │   │
│                                     └────────┬───────┘   │
│                                              ▼           │
│                                       Ranked Results     │
└──────────────────────────────────────────────────────────┘
```

## 📋 Prerequisites

- **Python** 3.10+
- **Redis** server running locally (default: `localhost:6379`)
- **MongoDB** — either:
  - Local MongoDB (`localhost:27017`), or
  - MongoDB Atlas free tier (M0) for `$vectorSearch` support

## 🚀 Setup

### 1. Clone the repository

```bash
git clone https://github.com/sigmakiller/search-engine.git
cd search-engine
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment (optional)

Create a `.env` file or set environment variables:

```bash
# MongoDB Atlas (recommended for $vectorSearch)
MONGO_URI=mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/
USE_ATLAS_SEARCH=true

# Or use local MongoDB (default, no config needed)
# MONGO_URI=mongodb://localhost:27017/

# Customize crawl behavior
MAX_PAGES=30000
MAX_CONCURRENT_REQUESTS=10
DEFAULT_CRAWL_DELAY=1.0
```

See [`config.py`](config.py) for all available settings.

### 5. Atlas Vector Search Index (if using Atlas)

If using MongoDB Atlas, create a vector search index in the Atlas UI:

1. Go to your cluster → **Atlas Search** → **Create Index**
2. Select **JSON Editor** and use this definition:

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "vector",
      "numDimensions": 384,
      "similarity": "cosine"
    }
  ]
}
```

3. Name the index `vector_index` (or match `VECTOR_INDEX_NAME` in config)
4. Apply to the `pages` collection in the `search_engine` database

## 📖 Usage

### Start the Crawler

```bash
python crawler.py
```

Output:
```
Starting async crawler with 10 workers...
Target: 30000 pages

[SEED] https://en.wikipedia.org/wiki/Artificial_intelligence
[SEED] https://www.python.org
...

[ROBOTS] Loaded robots.txt for https://en.wikipedia.org
[W0] ✓ Stored (1/30000): https://en.wikipedia.org/wiki/Artificial_intelligence
[W3] ✓ Stored (2/30000): https://www.python.org
...
```

Stop anytime with `Ctrl+C` — the crawler shuts down gracefully.

### Start the Search API

```bash
# Option 1: Direct
python search_api.py

# Option 2: Uvicorn with hot reload
uvicorn search_api:app --reload
```

The API will be available at `http://localhost:8000`.

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/search?q=...&page=1&limit=10` | Search with ranked results |
| `GET` | `/stats` | Index and crawl statistics |
| `GET` | `/health` | Service connectivity check |
| `GET` | `/docs` | Interactive Swagger documentation |

### Example Search

```bash
curl "http://localhost:8000/search?q=artificial+intelligence&limit=5"
```

Response:
```json
{
  "query": "artificial intelligence",
  "total_results": 42,
  "page": 1,
  "limit": 5,
  "elapsed_ms": 85.3,
  "results": [
    {
      "url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
      "title": "Artificial intelligence - Wikipedia",
      "snippet": "Artificial intelligence (AI) is intelligence demonstrated by machines...",
      "score": 0.8723,
      "vector_score": 0.9412,
      "backlink_score": 0.6521,
      "text_match_score": 1.0,
      "backlink_count": 12
    }
  ]
}
```

## 📁 Project Structure

```
search-engine/
├── config.py           # Centralized configuration (env vars + defaults)
├── crawler.py          # Async BFS web crawler with worker pool
├── robots_manager.py   # robots.txt compliance manager (async, cached)
├── ranker.py           # Hybrid ranking engine (vector + backlinks + text)
├── search_api.py       # FastAPI search endpoint
├── requirements.txt    # Python dependencies
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## ⚙️ Configuration Reference

All settings are in [`config.py`](config.py) and can be overridden via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGO_URI` | `mongodb://localhost:27017/` | MongoDB connection string |
| `USE_ATLAS_SEARCH` | `false` | Enable Atlas `$vectorSearch` |
| `REDIS_HOST` | `localhost` | Redis server host |
| `MAX_CONCURRENT_REQUESTS` | `10` | Total concurrent crawler workers |
| `MAX_CONCURRENT_PER_DOMAIN` | `2` | Max concurrent requests per domain |
| `DEFAULT_CRAWL_DELAY` | `1.0` | Seconds between requests (per domain) |
| `MAX_PAGES` | `30000` | Stop crawling after N pages |
| `MAX_RETRIES` | `3` | Retry failed requests N times |
| `API_PORT` | `8000` | Search API port |

## 🔬 Ranking Algorithm

Results are scored using a weighted combination of three signals:

```
final_score = (0.50 × vector_similarity)
            + (0.30 × backlink_score)
            + (0.20 × text_match_score)
```

| Signal | Weight | Description |
|--------|--------|-------------|
| **Vector Similarity** | 50% | Cosine similarity between query and page embeddings |
| **Backlink Score** | 30% | `log(1 + count) / log(1 + max)` — normalized authority |
| **Text Match** | 20% | Ratio of query terms found in page content |

Weights are configurable in `config.py`.

## 📄 License

This project is open source. Feel free to use, modify, and distribute.
