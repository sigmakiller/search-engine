import os
import asyncio
import aiohttp
import re
from bs4 import BeautifulSoup
import redis
import boto3
import io
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode
import time
from prometheus_client import start_http_server, Counter, Gauge
import config
from robots_manager import RobotsManager
from celery_app import app as celery_app

# ─── Prometheus Metrics ─────────────────────────────────────────────────────

PAGES_FETCHED = Counter('fetcher_pages_fetched_total', 'Total pages successfully fetched and saved to MinIO')
FETCH_ERRORS = Counter('fetcher_errors_total', 'Total fetching errors encountered')
QUEUE_SIZE = Gauge('fetcher_queue_size', 'Current number of URLs in the asyncio queue')


# ─── URL Normalization ───────────────────────────────────────────────────────

def normalize_url(url):
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower() if parsed.hostname else ""
        port = parsed.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        netloc = f"{host}:{port}" if port else host
        path = re.sub(r'/+', '/', parsed.path)
        if path != '/' and path.endswith('/'):
            path = path.rstrip('/')
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        sorted_query = urlencode(sorted(query_params.items()), doseq=True)
        normalized = urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))
        return normalized
    except Exception:
        return url


# ─── Shared Resources ───────────────────────────────────────────────────────

redis_client = redis.from_url(
    config.CELERY_BROKER_URL,
    decode_responses=True,
)

robots = RobotsManager()

# MinIO Client
s3_kwargs = {
    "aws_access_key_id": config.MINIO_ACCESS_KEY,
    "aws_secret_access_key": config.MINIO_SECRET_KEY,
    "region_name": os.getenv("MINIO_REGION", "us-east-1"),
}
if config.MINIO_ENDPOINT and "amazonaws.com" not in config.MINIO_ENDPOINT:
    s3_kwargs["endpoint_url"] = f"http://{config.MINIO_ENDPOINT}" if not config.MINIO_SECURE else f"https://{config.MINIO_ENDPOINT}"

s3_client = boto3.client("s3", **s3_kwargs)

# Ensure bucket exists
try:
    s3_client.head_bucket(Bucket=config.MINIO_BUCKET_NAME)
except Exception:
    try:
        s3_client.create_bucket(Bucket=config.MINIO_BUCKET_NAME)
    except Exception as e:
        print(f"Error creating bucket {config.MINIO_BUCKET_NAME}: {e}")

# ─── Per-Domain Rate Limiter ─────────────────────────────────────────────────

class DomainRateLimiter:
    def __init__(self):
        self._semaphores = {}
        self._last_request = {}

    def _get_domain(self, url):
        return urlparse(url).netloc

    async def acquire(self, url):
        domain = self._get_domain(url)
        if domain not in self._semaphores:
            self._semaphores[domain] = asyncio.Semaphore(config.MAX_CONCURRENT_PER_DOMAIN)
        await self._semaphores[domain].acquire()
        delay = robots.get_crawl_delay(url)
        last = self._last_request.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request[domain] = time.time()

    def release(self, url):
        domain = self._get_domain(url)
        if domain in self._semaphores:
            self._semaphores[domain].release()

rate_limiter = DomainRateLimiter()

# ─── Page Fetching ───────────────────────────────────────────────────────────

async def fetch_page(session, url):
    for attempt in range(config.MAX_RETRIES):
        try:
            await rate_limiter.acquire(url)
            headers = {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html",
                "Connection": "keep-alive",
            }
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT), headers=headers) as response:
                    if response.status == 200 and "text/html" in response.headers.get("Content-Type", ""):
                        try:
                            return await response.text(errors="replace")
                        except UnicodeDecodeError:
                            raw = await response.read()
                            return raw.decode("utf-8", errors="replace")
                    else:
                        return None
            finally:
                rate_limiter.release(url)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            rate_limiter.release(url)
            if attempt < config.MAX_RETRIES - 1:
                wait = config.RETRY_BACKOFF_BASE ** attempt
                await asyncio.sleep(wait)
            else:
                return None
        except Exception:
            rate_limiter.release(url)
            return None
    return None

def extract_links(url, html):
    soup = BeautifulSoup(html, "html.parser")
    outgoing_links = []
    for link in soup.find_all("a", href=True):
        raw_url = urljoin(url, link["href"]).split("#")[0]
        if raw_url.startswith("http"):
            norm_url = normalize_url(raw_url)
            outgoing_links.append(norm_url)
    return list(dict.fromkeys(outgoing_links))

async def worker(worker_id, queue, session, stats):
    while True:
        raw_url = await queue.get()
        try:
            url = normalize_url(raw_url)
            if redis_client.sismember("visited_urls", url):
                continue
            if stats["pages_crawled"] >= config.MAX_PAGES:
                continue
            if not await robots.can_fetch(session, url):
                redis_client.sadd("visited_urls", url)
                continue

            html = await fetch_page(session, url)
            if not html:
                continue

            # Save to MinIO
            object_key = f"{urlparse(url).netloc}/{hash(url)}.html"
            s3_client.put_object(
                Bucket=config.MINIO_BUCKET_NAME,
                Key=object_key,
                Body=html.encode('utf-8', errors='replace'),
                ContentType='text/html'
            )

            # Trigger Celery Task
            celery_app.send_task("indexer.process_html", args=[url, object_key])

            # Extract Links for BFS
            loop = asyncio.get_event_loop()
            discovered_urls = await loop.run_in_executor(None, extract_links, url, html)

            redis_client.sadd("visited_urls", url)
            stats["pages_crawled"] += 1
            PAGES_FETCHED.inc()

            print(f"[W{worker_id}] ✓ Fetched & Queued ({stats['pages_crawled']}/{config.MAX_PAGES}): {url}")

            for new_url in discovered_urls:
                redis_client.hset(f"backlinks:{new_url}", url, 1)
                if not redis_client.sismember("visited_urls", new_url):
                    if await robots.can_fetch(session, new_url):
                        await queue.put(new_url)
                        stats["urls_queued"] += 1

        except Exception as e:
            print(f"[W{worker_id}] Error processing {url}: {e}")
            FETCH_ERRORS.inc()
        finally:
            queue.task_done()
            QUEUE_SIZE.set(queue.qsize())

async def crawl():
    print(f"Starting async fetcher with {config.MAX_CONCURRENT_REQUESTS} workers...")
    stats = {"pages_crawled": 0, "urls_queued": 0, "start_time": time.time()}
    queue = asyncio.Queue()

    for seed in config.SEED_URLS:
        norm_seed = normalize_url(seed)
        if not redis_client.sismember("visited_urls", norm_seed):
            await queue.put(seed)

    while True:
        url = redis_client.rpop("to_crawl")
        if not url:
            break
        await queue.put(url)

    connector = aiohttp.TCPConnector(limit=config.MAX_CONCURRENT_REQUESTS, limit_per_host=config.MAX_CONCURRENT_PER_DOMAIN)
    async with aiohttp.ClientSession(connector=connector) as session:
        workers = [asyncio.create_task(worker(i, queue, session, stats)) for i in range(config.MAX_CONCURRENT_REQUESTS)]
        try:
            while stats["pages_crawled"] < config.MAX_PAGES:
                QUEUE_SIZE.set(queue.qsize())
                if queue.empty():
                    await asyncio.sleep(2)
                    if queue.empty():
                        break
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            pass
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

if __name__ == "__main__":
    try:
        start_http_server(8001)
        asyncio.run(crawl())
    except KeyboardInterrupt:
        pass
