"""
Robots.txt compliance manager.

Fetches, parses, and caches robots.txt files per domain using the Protego library.
Provides permission checking and crawl-delay awareness for the crawler.
"""

import time
import requests
from urllib.parse import urlparse
from protego import Protego
import config


class RobotsManager:
    """Manages robots.txt rules for all encountered domains."""

    def __init__(self, cache_ttl=3600):
        """
        Args:
            cache_ttl: How long (seconds) to cache a domain's robots.txt before re-fetching.
                       Default: 1 hour.
        """
        self._cache = {}        # domain -> {"parser": Protego, "fetched_at": float}
        self._cache_ttl = cache_ttl
        self._blocked_domains = set()  # Domains that returned 403 (fully disallowed)

    def _get_domain(self, url):
        """Extract the scheme + netloc (e.g., 'https://example.com')."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _fetch_robots_txt(self, domain):
        """
        Fetch and parse robots.txt for a domain.
        
        Returns:
            Protego parser instance, or None if the domain should be blocked.
        """
        robots_url = f"{domain}/robots.txt"
        try:
            response = requests.get(
                robots_url,
                timeout=config.REQUEST_TIMEOUT,
                headers={"User-Agent": config.USER_AGENT},
            )

            if response.status_code == 200:
                parser = Protego.parse(response.text)
                print(f"[ROBOTS] Loaded robots.txt for {domain}")
                return parser

            elif response.status_code == 403:
                # 403 on robots.txt = assume everything is disallowed
                print(f"[ROBOTS] 403 Forbidden for {domain}/robots.txt — blocking domain")
                self._blocked_domains.add(domain)
                return None

            elif response.status_code == 404:
                # No robots.txt = everything is allowed
                print(f"[ROBOTS] No robots.txt for {domain} (404) — all URLs allowed")
                return Protego.parse("")  # Empty rules = allow all

            else:
                print(f"[ROBOTS] Unexpected status {response.status_code} for {robots_url}")
                return Protego.parse("")  # Permissive fallback

        except Exception as e:
            print(f"[ROBOTS] Failed to fetch {robots_url}: {e}")
            return Protego.parse("")  # Allow on network error (be permissive)

    def _get_parser(self, url):
        """
        Get the cached Protego parser for a URL's domain.
        Fetches and caches if not already present or if cache is expired.
        
        Returns:
            Protego parser instance, or None if the domain is blocked.
        """
        domain = self._get_domain(url)

        # Check if domain is permanently blocked
        if domain in self._blocked_domains:
            return None

        # Check cache
        cached = self._cache.get(domain)
        if cached:
            age = time.time() - cached["fetched_at"]
            if age < self._cache_ttl:
                return cached["parser"]
            # Cache expired, re-fetch
            print(f"[ROBOTS] Cache expired for {domain}, re-fetching")

        # Fetch and cache
        parser = self._fetch_robots_txt(domain)
        if parser is not None:
            self._cache[domain] = {
                "parser": parser,
                "fetched_at": time.time(),
            }
        return parser

    def can_fetch(self, url):
        """
        Check if the crawler is allowed to fetch the given URL.
        
        Returns:
            True if allowed, False if disallowed by robots.txt or domain is blocked.
        """
        parser = self._get_parser(url)
        if parser is None:
            return False  # Domain is blocked

        allowed = parser.can_fetch(url, config.BOT_NAME)

        # Also check with wildcard user-agent as fallback
        if allowed:
            allowed = parser.can_fetch(url, "*")

        if not allowed:
            print(f"[ROBOTS] Blocked by robots.txt: {url}")

        return allowed

    def get_crawl_delay(self, url):
        """
        Get the crawl delay for a URL's domain.
        
        Returns:
            Crawl delay in seconds. Falls back to config.DEFAULT_CRAWL_DELAY
            if no delay is specified in robots.txt.
        """
        parser = self._get_parser(url)
        if parser is None:
            return config.DEFAULT_CRAWL_DELAY

        # Check delay for our bot name first, then wildcard
        delay = parser.crawl_delay(config.BOT_NAME)
        if delay is None:
            delay = parser.crawl_delay("*")
        if delay is None:
            delay = config.DEFAULT_CRAWL_DELAY

        return float(delay)

    def get_sitemaps(self, url):
        """
        Get sitemap URLs listed in a domain's robots.txt.
        
        Returns:
            List of sitemap URLs, or empty list if none found.
        """
        parser = self._get_parser(url)
        if parser is None:
            return []
        return list(parser.sitemaps)

    @property
    def stats(self):
        """Return current cache statistics."""
        return {
            "cached_domains": len(self._cache),
            "blocked_domains": len(self._blocked_domains),
            "blocked_list": list(self._blocked_domains),
        }
