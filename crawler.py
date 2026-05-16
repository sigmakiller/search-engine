"""
Async web crawler with BFS traversal, robots.txt compliance,
per-domain rate limiting, and retry with exponential backoff.
"""

import asyncio
import aiohttp
import re
from bs4 import BeautifulSoup
import redis
from sentence_transformers import SentenceTransformer
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode
import time
from pymongo import MongoClient
import config
from robots_manager import RobotsManager


# ─── URL Normalization ───────────────────────────────────────────────────────

def normalize_url(url):
    """
    Normalize a URL to prevent duplicate crawling of the same page.

    Normalization steps:
      1. Strip fragments (#section)
      2. Lowercase scheme and host
      3. Remove trailing slash (except for root paths)
      4. Sort query parameters alphabetically
      5. Remove default ports (80 for http, 443 for https)
      6. Collapse duplicate slashes in path

    Returns:
        Normalized URL string.
    """
    try:
        parsed = urlparse(url)

        # Lowercase scheme and host
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower() if parsed.hostname else ""

        # Remove default ports
        port = parsed.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        netloc = f"{host}:{port}" if port else host

        # Clean path: collapse duplicate slashes, remove trailing slash
        path = re.sub(r'/+', '/', parsed.path)
        if path != '/' and path.endswith('/'):
            path = path.rstrip('/')

        # Sort query parameters
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        sorted_query = urlencode(
            sorted(query_params.items()),
            doseq=True,
        )

        # Rebuild without fragment
        normalized = urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))
        return normalized

    except Exception:
        return url  # Return as-is if normalization fails


# ─── Shared Resources ───────────────────────────────────────────────────────

# Load embedding model
model = SentenceTransformer(config.MODEL_NAME)

# Redis connection
redis_client = redis.Redis(
    host=config.REDIS_HOST,
    port=config.REDIS_PORT,
    db=config.REDIS_DB,
    decode_responses=True,
)

# MongoDB connection
mongo_client = MongoClient(config.MONGO_URI)
db = mongo_client[config.MONGO_DB_NAME]
pages_collection = db[config.MONGO_COLLECTION]

# Robots.txt manager
robots = RobotsManager()


# ─── Per-Domain Rate Limiter ─────────────────────────────────────────────────

class DomainRateLimiter:
    """
    Enforces per-domain concurrency limits and crawl delays.
    Each domain gets its own semaphore (max concurrent requests)
    and a tracked last-request time to enforce crawl-delay.
    """

    def __init__(self):
        self._semaphores = {}   # domain -> asyncio.Semaphore
        self._last_request = {} # domain -> float (timestamp)

    def _get_domain(self, url):
        parsed = urlparse(url)
        return parsed.netloc

    async def acquire(self, url):
        """
        Wait until we're allowed to make a request to this URL's domain.
        Enforces both concurrency limit and crawl delay.
        """
        domain = self._get_domain(url)

        # Create semaphore for new domains
        if domain not in self._semaphores:
            self._semaphores[domain] = asyncio.Semaphore(
                config.MAX_CONCURRENT_PER_DOMAIN
            )

        # Acquire the semaphore (limits concurrent requests to same domain)
        await self._semaphores[domain].acquire()

        # Enforce crawl delay since last request to this domain
        delay = robots.get_crawl_delay(url)
        last = self._last_request.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)

        # Record this request time
        self._last_request[domain] = time.time()

    def release(self, url):
        """Release the semaphore for a URL's domain."""
        domain = self._get_domain(url)
        if domain in self._semaphores:
            self._semaphores[domain].release()


rate_limiter = DomainRateLimiter()


# ─── Page Fetching ───────────────────────────────────────────────────────────

async def fetch_page(session, url):
    """
    Fetch a webpage with retry and exponential backoff.

    Returns:
        HTML string on success, None on failure.
    """
    for attempt in range(config.MAX_RETRIES):
        try:
            await rate_limiter.acquire(url)
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT),
                    headers={"User-Agent": config.USER_AGENT},
                ) as response:
                    if (
                        response.status == 200
                        and "text/html" in response.headers.get("Content-Type", "")
                    ):
                        return await response.text()
                    else:
                        return None
            finally:
                rate_limiter.release(url)

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            rate_limiter.release(url)
            if attempt < config.MAX_RETRIES - 1:
                wait = config.RETRY_BACKOFF_BASE ** attempt
                print(f"[RETRY] Attempt {attempt + 1}/{config.MAX_RETRIES} failed for {url}: {e}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                print(f"[FAIL] All {config.MAX_RETRIES} attempts failed for {url}: {e}")
                return None

        except Exception as e:
            rate_limiter.release(url)
            print(f"[ERROR] Unexpected error fetching {url}: {e}")
            return None

    return None


# ─── Content Extraction ──────────────────────────────────────────────────────

def extract_about(soup):
    """Extract meta description or about content from a page."""
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        return desc["content"]

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        return og_desc["content"]

    p = soup.find("p")
    if p:
        return p.get_text().strip()

    return ""


def extract_body_text(soup):
    """
    Extract visible body text from a page, excluding scripts, styles,
    and other non-visible elements.

    Returns:
        Cleaned body text as a single string.
    """
    # Remove non-visible elements
    for tag in soup.find_all(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    # Get visible text
    text = soup.get_text(separator=" ", strip=True)

    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def process_page_data(url, html):
    """
    Extract useful data from a page's HTML.
    Extracts title, meta description, full body text, and creates
    a rich embedding from combined content.

    Returns:
        Tuple of (page_data dict, list of discovered normalized URLs).
    """
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    about = extract_about(soup)

    # Extract full body text
    body_text = extract_body_text(soup)
    body_snippet = body_text[:config.BODY_SNIPPET_LENGTH] if body_text else ""

    # Create rich embedding from combined content (title + about + body)
    embed_text = f"{title} {about} {body_text[:config.BODY_EMBED_LENGTH]}".strip()
    vector = model.encode(embed_text).tolist() if embed_text else []

    # Collect outgoing links (normalized)
    outgoing_links = []
    discovered_urls = []
    for link in soup.find_all("a", href=True):
        raw_url = urljoin(url, link["href"]).split("#")[0]
        if raw_url.startswith("http"):
            norm_url = normalize_url(raw_url)
            outgoing_links.append(norm_url)
            discovered_urls.append(norm_url)

    # Deduplicate outgoing links list
    outgoing_links = list(dict.fromkeys(outgoing_links))

    # Collect backlinks for this page from Redis
    norm_url = normalize_url(url)
    backlinks = list(redis_client.hkeys(f"backlinks:{norm_url}"))

    page_data = {
        "url": norm_url,
        "title": title,
        "about": about,
        "body_snippet": body_snippet,
        "vector": vector,
        "outgoing_links": outgoing_links,
        "backlinks": backlinks,
        "timestamp": time.time(),
    }

    return page_data, discovered_urls


# ─── Worker & Crawl Loop ────────────────────────────────────────────────────

async def worker(worker_id, queue, session, stats):
    """
    Async worker that pulls URLs from the queue and processes them.

    Args:
        worker_id: Numeric ID for logging.
        queue: asyncio.Queue of URLs to crawl.
        session: Shared aiohttp.ClientSession.
        stats: Shared dict for tracking crawl statistics.
    """
    while True:
        raw_url = await queue.get()

        try:
            # Normalize URL for deduplication
            url = normalize_url(raw_url)

            # Skip if already visited
            if redis_client.sismember("visited_urls", url):
                continue

            # Check page limit
            if stats["pages_crawled"] >= config.MAX_PAGES:
                continue

            # Check robots.txt permission (use original URL for accurate matching)
            if not await robots.can_fetch(session, url):
                redis_client.sadd("visited_urls", url)
                continue

            # Fetch the page
            html = await fetch_page(session, url)
            if not html:
                continue

            # Process the page (CPU-bound, run in thread pool)
            loop = asyncio.get_event_loop()
            page_data, discovered_urls = await loop.run_in_executor(
                None, process_page_data, url, html
            )

            # Store in MongoDB (keyed by normalized URL)
            pages_collection.update_one(
                {"url": url}, {"$set": page_data}, upsert=True
            )
            redis_client.sadd("visited_urls", url)
            stats["pages_crawled"] += 1

            print(
                f"[W{worker_id}] ✓ Stored ({stats['pages_crawled']}/{config.MAX_PAGES}): {url}"
            )

            # Enqueue discovered URLs (already normalized by process_page_data)
            for new_url in discovered_urls:
                # Register backlink (normalized source → normalized target)
                redis_client.hset(f"backlinks:{new_url}", url, 1)

                # Only enqueue if not visited and allowed
                if not redis_client.sismember("visited_urls", new_url):
                    if await robots.can_fetch(session, new_url):
                        await queue.put(new_url)
                        stats["urls_queued"] += 1

        except Exception as e:
            print(f"[W{worker_id}] Error processing {url}: {e}")

        finally:
            queue.task_done()


async def crawl():
    """Main async crawl orchestrator."""
    print(f"Starting async crawler with {config.MAX_CONCURRENT_REQUESTS} workers...")
    print(f"Target: {config.MAX_PAGES} pages\n")

    # Stats tracking
    stats = {
        "pages_crawled": 0,
        "urls_queued": 0,
        "start_time": time.time(),
    }

    # Create the URL queue
    queue = asyncio.Queue()

    # Seed the queue
    # Seed the queue — only add seeds that haven't been visited yet
    new_seeds = 0
    for seed in config.SEED_URLS:
        norm_seed = normalize_url(seed)
        if not redis_client.sismember("visited_urls", norm_seed):
            await queue.put(seed)
            print(f"[SEED] {seed}")
            new_seeds += 1
        else:
            print(f"[SKIP] Already visited: {seed}")

    # Also drain any pending URLs from Redis queue (from a previous run)
    restored = 0
    while True:
        url = redis_client.rpop("to_crawl")
        if not url:
            break
        await queue.put(url)
        restored += 1
    if restored:
        print(f"[QUEUE] Restored {restored} pending URLs from previous run")

    print(f"\n[INFO] New seeds: {new_seeds} | Restored from queue: {restored} | Total: {queue.qsize()}\n")

    # Create shared HTTP session
    connector = aiohttp.TCPConnector(
        limit=config.MAX_CONCURRENT_REQUESTS,
        limit_per_host=config.MAX_CONCURRENT_PER_DOMAIN,
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        # Spawn workers
        workers = [
            asyncio.create_task(worker(i, queue, session, stats))
            for i in range(config.MAX_CONCURRENT_REQUESTS)
        ]

        # Wait for the queue to be fully processed or MAX_PAGES hit
        try:
            while stats["pages_crawled"] < config.MAX_PAGES:
                if queue.empty():
                    # Wait a bit to see if new URLs arrive from workers
                    await asyncio.sleep(2)
                    if queue.empty():
                        print("\nQueue empty, no more URLs to crawl.")
                        break
                await asyncio.sleep(0.5)

        except KeyboardInterrupt:
            print("\n\nKeyboardInterrupt received. Shutting down gracefully...")

        # Cancel all workers
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    # Print final report
    elapsed = time.time() - stats["start_time"]
    print(f"\n{'='*60}")
    print(f"  CRAWL COMPLETE")
    print(f"{'='*60}")
    print(f"  Pages crawled : {stats['pages_crawled']}")
    print(f"  URLs queued   : {stats['urls_queued']}")
    print(f"  Time elapsed  : {elapsed:.1f}s")
    print(f"  Speed         : {stats['pages_crawled'] / max(elapsed, 1):.1f} pages/sec")
    print(f"  Robots stats  : {robots.stats}")
    print(f"{'='*60}")


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(crawl())
    except KeyboardInterrupt:
        print("\nCrawler stopped.")
