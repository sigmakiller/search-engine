"""
Ranking layer for the search engine.

Combines three signals to produce a final relevance score:
  1. Vector similarity  — cosine similarity between query and page embeddings
  2. Backlink score     — log-normalized count of incoming links (authority signal)
  3. Text match score   — keyword overlap between query and page content

Supports both MongoDB Atlas ($vectorSearch) and local (NumPy) vector search.
"""

import math
import re
import numpy as np
from sentence_transformers import SentenceTransformer
from pymongo import MongoClient
import config


# ─── Initialization ──────────────────────────────────────────────────────────

import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import torch
torch.set_num_threads(1)

model = SentenceTransformer(config.MODEL_NAME)

import sys
print(f"DEBUG MONGO_URI: {repr(config.MONGO_URI)}", file=sys.stderr)

mongo_client = MongoClient(config.MONGO_URI)
db = mongo_client[config.MONGO_DB_NAME]
pages_collection = db[config.MONGO_COLLECTION]


# ─── Vector Similarity ──────────────────────────────────────────────────────

def cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two vectors."""
    a = np.array(vec_a)
    b = np.array(vec_b)
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _atlas_vector_search(query_vector, limit, num_candidates=None):
    """
    Perform vector search using MongoDB Atlas $vectorSearch.
    Requires a vector search index named config.VECTOR_INDEX_NAME.

    Returns:
        List of dicts with page data and 'vector_score' field.
    """
    if num_candidates is None:
        num_candidates = config.VECTOR_SEARCH_CANDIDATES

    pipeline = [
        {
            "$vectorSearch": {
                "index": config.VECTOR_INDEX_NAME,
                "path": "vector",
                "queryVector": query_vector,
                "numCandidates": num_candidates,
                "limit": limit,
            }
        },
        {
            "$addFields": {
                "vector_score": {"$meta": "vectorSearchScore"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "url": 1,
                "title": 1,
                "about": 1,
                "body_snippet": 1,
                "vector_score": 1,
                "backlinks": 1,
                "outgoing_links": 1,
            }
        },
    ]

    results = list(pages_collection.aggregate(pipeline))
    return results


def _local_vector_search(query_vector, limit):
    """
    Perform vector search using NumPy cosine similarity (local MongoDB fallback).
    Loads all vectors from MongoDB and computes similarity in Python.

    Returns:
        List of dicts with page data and 'vector_score' field.
    """
    # Fetch all pages with vectors
    cursor = pages_collection.find(
        {"vector": {"$exists": True, "$ne": []}},
        {
            "_id": 0,
            "url": 1,
            "title": 1,
            "about": 1,
            "body_snippet": 1,
            "vector": 1,
            "backlinks": 1,
            "outgoing_links": 1,
        },
    )

    scored_pages = []
    for page in cursor:
        score = cosine_similarity(query_vector, page["vector"])
        page["vector_score"] = score
        del page["vector"]  # Don't return the raw vector
        scored_pages.append(page)

    # Sort by vector score descending, take top N
    scored_pages.sort(key=lambda x: x["vector_score"], reverse=True)
    return scored_pages[:limit]


# ─── Backlink Score ──────────────────────────────────────────────────────────

def compute_backlink_score(backlink_count, max_backlinks):
    """
    Compute a normalized backlink authority score.

    Uses log scale to prevent pages with massive backlink counts
    from dominating. Normalized to [0, 1] range.

    Args:
        backlink_count: Number of backlinks for this page.
        max_backlinks: Maximum backlink count across all candidate pages.

    Returns:
        Float between 0.0 and 1.0.
    """
    if max_backlinks <= 0:
        return 0.0
    return math.log(1 + backlink_count) / math.log(1 + max_backlinks)


# ─── Text Match Score ────────────────────────────────────────────────────────

def compute_text_match_score(query, title, about, body_snippet):
    """
    Compute keyword overlap between the query and page content.

    Checks how many unique query terms appear in the combined
    title + about + body_snippet text.

    Args:
        query: The search query string.
        title: Page title.
        about: Page meta description.
        body_snippet: First N characters of page body text.

    Returns:
        Float between 0.0 and 1.0 (ratio of matched query terms).
    """
    # Tokenize query into lowercase terms
    query_terms = set(re.findall(r'\w+', query.lower()))
    if not query_terms:
        return 0.0

    # Combine page content into lowercase searchable text
    content = f"{title} {about} {body_snippet}".lower()

    # Count how many query terms appear in the content
    matched = sum(1 for term in query_terms if term in content)

    return matched / len(query_terms)


# ─── Main Ranking Function ──────────────────────────────────────────────────

def search(query, limit=None, use_atlas=None):
    """
    Search for pages matching the query and return ranked results.

    Scoring formula:
        final_score = (w1 × vector_similarity)
                    + (w2 × backlink_score)
                    + (w3 × text_match_score)

    Args:
        query: Search query string.
        limit: Max number of results to return. Defaults to config.DEFAULT_SEARCH_LIMIT.
        use_atlas: Whether to use Atlas $vectorSearch. Defaults to config.USE_ATLAS_SEARCH.

    Returns:
        List of result dicts sorted by final_score descending, each containing:
            - url, title, about, body_snippet
            - score (final combined score)
            - vector_score, backlink_score, text_match_score (individual signals)
            - backlink_count
    """
    if limit is None:
        limit = config.DEFAULT_SEARCH_LIMIT
    if use_atlas is None:
        use_atlas = config.USE_ATLAS_SEARCH

    # Clamp limit
    limit = min(limit, config.MAX_SEARCH_LIMIT)

    # Generate query embedding
    query_vector = model.encode(query).tolist()

    # Retrieve candidates via vector search
    # Fetch more candidates than limit so re-ranking has enough data
    candidate_limit = min(limit * 5, config.MAX_SEARCH_LIMIT * 5)

    if use_atlas:
        candidates = _atlas_vector_search(query_vector, candidate_limit)
    else:
        candidates = _local_vector_search(query_vector, candidate_limit)

    if not candidates:
        return []

    # Find max backlink count for normalization
    max_backlinks = max(
        len(c.get("backlinks", [])) for c in candidates
    )

    # Score each candidate
    results = []
    for candidate in candidates:
        backlink_count = len(candidate.get("backlinks", []))
        vec_score = candidate.get("vector_score", 0.0)
        bl_score = compute_backlink_score(backlink_count, max_backlinks)
        txt_score = compute_text_match_score(
            query,
            candidate.get("title", ""),
            candidate.get("about", ""),
            candidate.get("body_snippet", ""),
        )

        # Combined score
        final_score = (
            config.WEIGHT_VECTOR_SIMILARITY * vec_score
            + config.WEIGHT_BACKLINK_SCORE * bl_score
            + config.WEIGHT_TEXT_MATCH * txt_score
        )

        results.append({
            "url": candidate.get("url", ""),
            "title": candidate.get("title", ""),
            "about": candidate.get("about", ""),
            "snippet": candidate.get("body_snippet", ""),
            "score": round(final_score, 4),
            "vector_score": round(vec_score, 4),
            "backlink_score": round(bl_score, 4),
            "text_match_score": round(txt_score, 4),
            "backlink_count": backlink_count,
        })

    # Sort by final score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:limit]


# ─── Stats ───────────────────────────────────────────────────────────────────

def get_index_stats():
    """Get statistics about the indexed pages."""
    total_pages = pages_collection.count_documents({})
    pages_with_vectors = pages_collection.count_documents(
        {"vector": {"$exists": True, "$ne": []}}
    )

    # Compute average backlink count
    pipeline = [
        {"$project": {"backlink_count": {"$size": {"$ifNull": ["$backlinks", []]}}}},
        {"$group": {
            "_id": None,
            "avg_backlinks": {"$avg": "$backlink_count"},
            "max_backlinks": {"$max": "$backlink_count"},
            "total_backlinks": {"$sum": "$backlink_count"},
        }},
    ]
    backlink_stats = list(pages_collection.aggregate(pipeline))

    stats = {
        "total_pages": total_pages,
        "pages_with_vectors": pages_with_vectors,
    }

    if backlink_stats:
        bl = backlink_stats[0]
        stats["avg_backlinks"] = round(bl.get("avg_backlinks", 0), 2)
        stats["max_backlinks"] = bl.get("max_backlinks", 0)
        stats["total_backlinks"] = bl.get("total_backlinks", 0)

    return stats
