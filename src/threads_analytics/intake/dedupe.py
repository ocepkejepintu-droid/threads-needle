"""Dedupe intake items by URL and title similarity."""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from .fetchers import RawIntakeItem

log = logging.getLogger(__name__)

# URL normalization
_SIMILARITY_THRESHOLD = 0.80


def _normalize_url(url: str) -> str:
    """Strip tracking params and trailing slashes for comparison."""
    url = url.split("?")[0].split("#")[0].rstrip("/")
    return url.lower()


def _title_similarity(a: str, b: str) -> float:
    """Return 0.0–1.0 similarity between two titles."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedupe_items(items: list[RawIntakeItem]) -> list[RawIntakeItem]:
    """Remove duplicates by exact URL match, then by title similarity > 80%."""
    seen_urls: set[str] = set()
    deduped: list[RawIntakeItem] = []

    for item in items:
        norm = _normalize_url(item.source_url)
        if norm in seen_urls:
            continue

        # Check title similarity against already-kept items
        is_duplicate = False
        for kept in deduped:
            if _title_similarity(item.source_title, kept.source_title) >= _SIMILARITY_THRESHOLD:
                is_duplicate = True
                break

        if is_duplicate:
            continue

        seen_urls.add(norm)
        deduped.append(item)

    log.info("Deduped %d items → %d unique", len(items), len(deduped))
    return deduped
