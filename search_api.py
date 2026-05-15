"""
Search Engine API — FastAPI application.

Endpoints:
    GET /search?q=...&page=1&limit=10  — Search with ranked results
    GET /stats                          — Index and crawl statistics
    GET /health                         — Health check
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import time
import redis
import config
import ranker


# ─── App Setup ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="MiniSearch Engine API",
    description="Semantic search engine with BFS crawling, vector embeddings, and hybrid ranking.",
    version="1.0.0",
)

# Allow CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redis connection (for crawl queue stats)
redis_client = redis.Redis(
    host=config.REDIS_HOST,
    port=config.REDIS_PORT,
    db=config.REDIS_DB,
    decode_responses=True,
)


# ─── Search Endpoint ────────────────────────────────────────────────────────

@app.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    page: int = Query(1, ge=1, le=100, description="Page number"),
    limit: int = Query(
        config.DEFAULT_SEARCH_LIMIT,
        ge=1,
        le=config.MAX_SEARCH_LIMIT,
        description="Results per page",
    ),
):
    """
    Search the indexed pages using semantic + keyword hybrid ranking.

    Returns ranked results with relevance scores and metadata.
    """
    start_time = time.time()

    try:
        # Fetch more results than needed for pagination
        total_needed = page * limit
        all_results = ranker.search(query=q, limit=total_needed)

        # Paginate
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        page_results = all_results[start_idx:end_idx]

        elapsed_ms = round((time.time() - start_time) * 1000, 1)

        return {
            "query": q,
            "total_results": len(all_results),
            "page": page,
            "limit": limit,
            "elapsed_ms": elapsed_ms,
            "results": page_results,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


# ─── Stats Endpoint ─────────────────────────────────────────────────────────

@app.get("/stats")
async def stats():
    """
    Get statistics about the search engine index and crawl state.

    Returns:
        - Index stats (total pages, vector coverage, backlink distribution)
        - Crawl queue stats (pending URLs, visited count)
        - Configuration summary
    """
    try:
        # Index stats from MongoDB
        index_stats = ranker.get_index_stats()

        # Crawl queue stats from Redis
        crawl_stats = {
            "queue_size": redis_client.llen("to_crawl"),
            "visited_urls": redis_client.scard("visited_urls"),
        }

        # Config summary
        config_summary = {
            "max_pages": config.MAX_PAGES,
            "max_concurrent_requests": config.MAX_CONCURRENT_REQUESTS,
            "max_concurrent_per_domain": config.MAX_CONCURRENT_PER_DOMAIN,
            "default_crawl_delay": config.DEFAULT_CRAWL_DELAY,
            "embedding_model": config.MODEL_NAME,
            "vector_dimensions": config.VECTOR_DIMENSIONS,
            "using_atlas_search": config.USE_ATLAS_SEARCH,
            "ranking_weights": {
                "vector_similarity": config.WEIGHT_VECTOR_SIMILARITY,
                "backlink_score": config.WEIGHT_BACKLINK_SCORE,
                "text_match": config.WEIGHT_TEXT_MATCH,
            },
        }

        return {
            "index": index_stats,
            "crawl": crawl_stats,
            "config": config_summary,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


# ─── Health Check ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """
    Health check endpoint.

    Verifies connectivity to Redis and MongoDB.
    """
    status = {"status": "healthy", "services": {}}

    # Check Redis
    try:
        redis_client.ping()
        status["services"]["redis"] = "connected"
    except Exception as e:
        status["status"] = "degraded"
        status["services"]["redis"] = f"error: {str(e)}"

    # Check MongoDB
    try:
        ranker.mongo_client.admin.command("ping")
        status["services"]["mongodb"] = "connected"
    except Exception as e:
        status["status"] = "degraded"
        status["services"]["mongodb"] = f"error: {str(e)}"

    # Check embedding model
    try:
        test_vec = ranker.model.encode("health check").tolist()
        status["services"]["embedding_model"] = f"loaded ({len(test_vec)} dims)"
    except Exception as e:
        status["status"] = "degraded"
        status["services"]["embedding_model"] = f"error: {str(e)}"

    status_code = 200 if status["status"] == "healthy" else 503
    return JSONResponse(content=status, status_code=status_code)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print(f"\n{'='*60}")
    print(f"  MiniSearch Engine API")
    print(f"  http://{config.API_HOST}:{config.API_PORT}")
    print(f"  Docs: http://localhost:{config.API_PORT}/docs")
    print(f"{'='*60}\n")

    uvicorn.run(
        "search_api:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=True,
    )
