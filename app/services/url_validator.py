from __future__ import annotations

from urllib.parse import urlparse

BLOCKED_DOMAINS = {
    "reddit.com",
    "www.reddit.com",
    "quora.com",
    "www.quora.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "pinterest.com",
    "www.pinterest.com",
    "wikipedia.org",
    "www.wikipedia.org",
}

BLOCKED_PATH_HINTS = [
    "/forum",
    "/forums",
    "/products",
    "/shop",
    "/category",
    "/tag",
]


def is_acceptable_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False

    host = parsed.netloc.lower()
    if host in BLOCKED_DOMAINS:
        return False

    path = parsed.path.lower()
    if any(hint in path for hint in BLOCKED_PATH_HINTS):
        return False

    return True


def select_top_urls(urls: list[str], max_urls: int) -> list[str]:
    filtered = [u for u in urls if is_acceptable_url(u)]
    return filtered[:max_urls]
