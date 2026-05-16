"""
Centralized configuration for the search engine crawler and API.
All settings are configurable via environment variables with sensible defaults.
"""

import os


# ─── Database ────────────────────────────────────────────────────────────────

# MongoDB Atlas connection (free tier M0)
# Set MONGO_URI env var to your Atlas connection string, e.g.:
#   mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/
# Falls back to local MongoDB if not set.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "search_engine")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "pages")

# Set to True when using Atlas (enables $vectorSearch instead of NumPy fallback)
USE_ATLAS_SEARCH = os.getenv("USE_ATLAS_SEARCH", "false").lower() == "true"

# Atlas vector search index name (must be created in Atlas UI)
VECTOR_INDEX_NAME = os.getenv("VECTOR_INDEX_NAME", "vector_index")

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))


# ─── Embedding Model ────────────────────────────────────────────────────────

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
VECTOR_DIMENSIONS = 384  # Output dimensions for all-MiniLM-L6-v2


# ─── Crawler Settings ───────────────────────────────────────────────────────

USER_AGENT = "Mozilla/5.0 (compatible; MiniSearchBot/1.0; +https://example.com/bot)"
BOT_NAME = "MiniSearchBot"  # Used for robots.txt matching

# Concurrency
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "10"))
MAX_CONCURRENT_PER_DOMAIN = int(os.getenv("MAX_CONCURRENT_PER_DOMAIN", "2"))

# Politeness
DEFAULT_CRAWL_DELAY = float(os.getenv("DEFAULT_CRAWL_DELAY", "1.0"))  # seconds
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))  # seconds

# Retry
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_BASE = 2  # exponential backoff: 2^attempt seconds

# Scale
MAX_PAGES = int(os.getenv("MAX_PAGES", "30000"))  # Stop crawling after this many pages

# Content extraction
BODY_SNIPPET_LENGTH = 500   # Characters to store for display snippets
BODY_EMBED_LENGTH = 1000    # Characters of body text to include in embedding


# ─── Search / Ranking ───────────────────────────────────────────────────────

# Weights for the ranking formula (must sum to 1.0)
WEIGHT_VECTOR_SIMILARITY = 0.50
WEIGHT_BACKLINK_SCORE = 0.30
WEIGHT_TEXT_MATCH = 0.20

# Search defaults
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
VECTOR_SEARCH_CANDIDATES = 200  # numCandidates for $vectorSearch (10-20x limit)


# ─── API Settings ────────────────────────────────────────────────────────────

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))


# ─── Seed URLs ───────────────────────────────────────────────────────────────

SEED_URLS = [
    # Knowledge
    "https://en.wikipedia.org/wiki/Artificial_intelligence",
    "https://en.wikipedia.org/wiki/Computer_science",
    "https://en.wikipedia.org/wiki/Machine_learning",
    "https://en.wikipedia.org/wiki/World_Wide_Web",
    "https://simple.wikipedia.org",

    # Tech / Docs
    "https://www.python.org",
    "https://docs.python.org/3/",
    "https://developer.mozilla.org/en-US/",
    "https://www.w3schools.com",
    "https://github.com/explore",
    "https://stackoverflow.com/questions",

    # Science
    "https://arxiv.org",
    "https://www.nasa.gov",
    "https://www.nature.com",

    # News / General
    "https://www.bbc.com/news",
    "https://www.reuters.com",
    "https://medium.com/tag/technology",
    "https://www.quora.com/What-is-artificial-intelligence-15",

    # Open Knowledge
    "https://archive.org",
    "https://www.gutenberg.org",
    "https://openlibrary.org",
    "https://www.britannica.com",
]
