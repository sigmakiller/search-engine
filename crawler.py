import requests
from bs4 import BeautifulSoup
import redis
from sentence_transformers import SentenceTransformer
import json
from urllib.parse import urljoin
import time
from pymongo import MongoClient
import config
from robots_manager import RobotsManager

# Load embedding model
model = SentenceTransformer(config.MODEL_NAME)

# Redis connection
r = redis.Redis(
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

def get_page_content(url):
    """Fetch and parse a webpage."""
    try:
        headers = {
            "User-Agent": config.USER_AGENT
        }
        response = requests.get(url, timeout=10, headers=headers)
        if response.status_code == 200 and "text/html" in response.headers.get("Content-Type", ""):
            return response.text
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
    return None

def extract_about(soup):
    """Extract description or about content."""
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

def process_page(url, html):
    """Extract useful data from page and store it."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    about = extract_about(soup)
    vector = model.encode(about).tolist() if about else []

    # Collect outgoing links
    outgoing_links = []
    for link in soup.find_all("a", href=True):
        new_url = urljoin(url, link["href"]).split("#")[0]
        if new_url.startswith("http"):
            outgoing_links.append(new_url)
            # Add to Redis queue if not already visited and allowed by robots.txt
            if not r.sismember("visited_urls", new_url) and robots.can_fetch(new_url):
                r.lpush("to_crawl", new_url)

            # Register backlink
            r.hset(f"backlinks:{new_url}", url, 1)

    # Collect backlinks for this page
    backlinks = list(r.hkeys(f"backlinks:{url}"))

    # Store in MongoDB
    page_data = {
        "url": url,
        "title": title,
        "about": about,
        "vector": vector,
        "outgoing_links": outgoing_links,
        "backlinks": backlinks,
        "timestamp": time.time(),
    }
    pages_collection.update_one({"url": url}, {"$set": page_data}, upsert=True)
    print(f"Successfully Stored: {url}")

def crawler_loop():
    """Run indefinitely until terminated or MAX_PAGES reached."""
    pages_crawled = 0

    while True:
        # Stop if we've hit the page limit
        if pages_crawled >= config.MAX_PAGES:
            print(f"Reached MAX_PAGES limit ({config.MAX_PAGES}). Stopping.")
            break

        url = r.rpop("to_crawl")
        if not url:
            print("No URLs left, waiting...")
            time.sleep(5)
            continue

        # Skip already visited
        if r.sismember("visited_urls", url):
            continue

        # Check robots.txt permission
        if not robots.can_fetch(url):
            r.sadd("visited_urls", url)  # Mark as visited so we don't retry
            continue

        html = get_page_content(url)
        if not html:
            continue

        process_page(url, html)
        r.sadd("visited_urls", url)
        pages_crawled += 1

        # Use per-domain crawl delay from robots.txt (or default)
        delay = robots.get_crawl_delay(url)
        time.sleep(delay)

    # Print final stats
    print(f"\nCrawl complete. Pages crawled: {pages_crawled}")
    print(f"Robots.txt stats: {robots.stats}")

if __name__ == "__main__":
    # Seed URLs if Redis queue is empty
    if r.llen("to_crawl") == 0:
        for seed in config.SEED_URLS:
            r.lpush("to_crawl", seed)

    crawler_loop()
