from celery_app import app
import os
import boto3
import time
from bs4 import BeautifulSoup
import re
from pymongo import MongoClient
import redis
from sentence_transformers import SentenceTransformer
import config
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, urljoin

# Global resources for the worker
s3_kwargs = {
    "aws_access_key_id": config.MINIO_ACCESS_KEY,
    "aws_secret_access_key": config.MINIO_SECRET_KEY,
    "region_name": os.getenv("MINIO_REGION", "us-east-1"),
}
if config.MINIO_ENDPOINT and "amazonaws.com" not in config.MINIO_ENDPOINT:
    s3_kwargs["endpoint_url"] = f"http://{config.MINIO_ENDPOINT}" if not config.MINIO_SECURE else f"https://{config.MINIO_ENDPOINT}"

s3_client = boto3.client("s3", **s3_kwargs)

mongo_client = MongoClient(config.MONGO_URI)
db = mongo_client[config.MONGO_DB_NAME]
pages_collection = db[config.MONGO_COLLECTION]

redis_client = redis.from_url(
    config.CELERY_BROKER_URL,
    decode_responses=True,
)

model = SentenceTransformer(config.MODEL_NAME)

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
        return urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))
    except Exception:
        return url

def extract_about(soup):
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"): return desc["content"]
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"): return og_desc["content"]
    p = soup.find("p")
    if p: return p.get_text().strip()
    return ""

def extract_body_text(soup):
    for tag in soup.find_all(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r'\s+', ' ', text).strip()

@app.task(name="indexer.process_html", bind=True, max_retries=3)
def process_html(self, url, object_key):
    try:
        response = s3_client.get_object(Bucket=config.MINIO_BUCKET_NAME, Key=object_key)
        html = response['Body'].read().decode('utf-8', errors='replace')
        
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        about = extract_about(soup)
        
        body_text = extract_body_text(soup)
        body_snippet = body_text[:config.BODY_SNIPPET_LENGTH] if body_text else ""
        
        embed_text = f"{title} {about} {body_text[:config.BODY_EMBED_LENGTH]}".strip()
        vector = model.encode(embed_text).tolist() if embed_text else []
        
        backlinks = list(redis_client.hkeys(f"backlinks:{url}"))

        outgoing_links = []
        for link in soup.find_all("a", href=True):
            raw_url = urljoin(url, link["href"]).split("#")[0]
            if raw_url.startswith("http"):
                norm_url = normalize_url(raw_url)
                outgoing_links.append(norm_url)
        outgoing_links = list(dict.fromkeys(outgoing_links))

        page_data = {
            "url": url,
            "title": title,
            "about": about,
            "body_snippet": body_snippet,
            "vector": vector,
            "outgoing_links": outgoing_links,
            "backlinks": backlinks,
            "timestamp": time.time(),
        }

        pages_collection.update_one({"url": url}, {"$set": page_data}, upsert=True)
        return {"status": "success", "url": url}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5)
