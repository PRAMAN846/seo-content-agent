from __future__ import annotations

import re

URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    return URL_RE.findall(text)


def collect_seed_urls(
    *,
    query: str,
    seed_urls: list[str],
    ai_citations_text: str,
    ai_overview_text: str,
) -> list[str]:
    del query  # Reserved for future optional search integration.
    collected = list(seed_urls)
    collected.extend(extract_urls_from_text(ai_citations_text))
    collected.extend(extract_urls_from_text(ai_overview_text))

    seen: set[str] = set()
    unique: list[str] = []
    for url in collected:
        cleaned = url.strip().rstrip("/ ")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique
