"""
Robots.txt compliance manager (async version).

Fetches, parses, and caches robots.txt files per domain using the Protego library.
Provides permission checking and crawl-delay awareness for the async crawler.
"""

import time
import asyncio
import aiohttp
from urllib.parse import urlparse
from protego import Protego
import config


class RobotsManager:
    """Manages robots.txt rules for all encountered domains (async)."""

    def __init__(self, cache_ttl=3600):
        """
        Args:
            cache_ttl: How long (seconds) to cache a domain's robots.txt before re-fetching.
                       Default: 1 hour.
        """
        self._cache = {}        # domain -> {"parser": Protego, "fetched_at": float}
        self._cache_ttl = cache_ttl
        self._blocked_domains = set()  # Domains that returned 403 (fully disallowed)
        self._locks = {}        # Per-domain asyncio.Lock to prevent duplicate fetches

    def _get_domain(self, url):
        """Extract the scheme + netloc (e.g., 'https://example.com')."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _get_lock(self, domain):
        """Get or create an asyncio.Lock for a domain to prevent race conditions."""
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def _fetch_robots_txt(self, session, domain):
        """
        Fetch and parse robots.txt for a domain.

        Args:
            session: aiohttp.ClientSession to use for the request.
            domain: Base URL of the domain (e.g., 'https://example.com').

        Returns:
            Protego parser instance, or None if the domain should be blocked.
        """
        robots_url = f"{domain}/robots.txt"
        try:
            async with session.get(
                robots_url,
                timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT),
                headers={"User-Agent": config.USER_AGENT},
            ) as response:

                if response.status == 200:
                    text = await response.text()
                    parser = Protego.parse(text)
                    print(f"[ROBOTS] Loaded robots.txt for {domain}")
                    return parser

                elif response.status == 403:
                    # 403 on robots.txt = assume everything is disallowed
                    print(f"[ROBOTS] 403 Forbidden for {domain}/robots.txt — blocking domain")
                    self._blocked_domains.add(domain)
                    return None

                elif response.status == 404:
                    # No robots.txt = everything is allowed
                    print(f"[ROBOTS] No robots.txt for {domain} (404) — all URLs allowed")
                    return Protego.parse("")  # Empty rules = allow all

                else:
                    print(f"[ROBOTS] Unexpected status {response.status} for {robots_url}")
                    return Protego.parse("")  # Permissive fallback

        except Exception as e:
            print(f"[ROBOTS] Failed to fetch {robots_url}: {e}")
            return Protego.parse("")  # Allow on network error (be permissive)

    async def _get_parser(self, session, url):
        """
        Get the cached Protego parser for a URL's domain.
        Fetches and caches if not already present or if cache is expired.
        Uses per-domain locks to prevent duplicate concurrent fetches.

        Args:
            session: aiohttp.ClientSession to use for fetching.
            url: The URL to get robots.txt rules for.

        Returns:
            Protego parser instance, or None if the domain is blocked.
        """
        domain = self._get_domain(url)

        # Check if domain is permanently blocked (no lock needed)
        if domain in self._blocked_domains:
            return None

        # Check cache (no lock needed for reads)
        cached = self._cache.get(domain)
        if cached:
            age = time.time() - cached["fetched_at"]
            if age < self._cache_ttl:
                return cached["parser"]

        # Acquire per-domain lock for fetching
        lock = self._get_lock(domain)
        async with lock:
            # Double-check cache after acquiring lock (another worker may have fetched)
            cached = self._cache.get(domain)
            if cached:
                age = time.time() - cached["fetched_at"]
                if age < self._cache_ttl:
                    return cached["parser"]

            # Fetch and cache
            parser = await self._fetch_robots_txt(session, domain)
            if parser is not None:
                self._cache[domain] = {
                    "parser": parser,
                    "fetched_at": time.time(),
                }
            return parser

    async def can_fetch(self, session, url):
        """
        Check if the crawler is allowed to fetch the given URL.

        Args:
            session: aiohttp.ClientSession to use if robots.txt needs fetching.
            url: The URL to check.

        Returns:
            True if allowed, False if disallowed by robots.txt or domain is blocked.
        """
        parser = await self._get_parser(session, url)
        if parser is None:
            return False  # Domain is blocked

        allowed = parser.can_fetch(url, config.BOT_NAME)

        if not allowed:
            print(f"[ROBOTS] Blocked by robots.txt: {url}")

        return allowed

    def get_crawl_delay(self, url):
        """
        Get the crawl delay for a URL's domain from the cached parser.
        This is synchronous since it only reads from the cache.

        Returns:
            Crawl delay in seconds. Falls back to config.DEFAULT_CRAWL_DELAY
            if no delay is specified or the domain isn't cached yet.
        """
        domain = self._get_domain(url)
        cached = self._cache.get(domain)
        if cached is None:
            return config.DEFAULT_CRAWL_DELAY

        parser = cached["parser"]

        # Check delay for our bot name first, then wildcard
        delay = parser.crawl_delay(config.BOT_NAME)
        if delay is None:
            delay = parser.crawl_delay("*")
        if delay is None:
            delay = config.DEFAULT_CRAWL_DELAY

        return float(delay)

    def get_sitemaps(self, url):
        """
        Get sitemap URLs listed in a domain's robots.txt (from cache).

        Returns:
            List of sitemap URLs, or empty list if none found.
        """
        domain = self._get_domain(url)
        cached = self._cache.get(domain)
        if cached is None:
            return []
        return list(cached["parser"].sitemaps)

    @property
    def stats(self):
        """Return current cache statistics."""
        return {
            "cached_domains": len(self._cache),
            "blocked_domains": len(self._blocked_domains),
            "blocked_list": list(self._blocked_domains),
        }
