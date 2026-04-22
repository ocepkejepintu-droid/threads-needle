"""Source fetchers for daily intake — HN, RSS feeds, manual sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger(__name__)

HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"

RSS_SOURCES = {
    "anthropic": "https://www.anthropic.com/rss.xml",
    "openai": "https://openai.com/blog/rss.xml",
}

# Optional sources that may fail gracefully
FALLBACK_RSS_SOURCES = {
    "gemini": "https://blog.google/products/gemini/rss/",
}

_AI_KEYWORDS = {
    "ai", "llm", "machine learning", "model", "gpt", "claude", "anthropic",
    "openai", "gemini", "deepmind", "token", "inference", "agent", "swarm",
    "fine-tune", "alignment", "prompt", "embedding", "vector", "rag",
    "multimodal", "computer use", "api", "pricing", "release", "benchmark",
}


@dataclass
class RawIntakeItem:
    source: str  # e.g. 'hn', 'anthropic', 'openai'
    source_url: str
    source_title: str
    raw_data: dict
    discovered_at: datetime


def _is_ai_relevant(title: str) -> bool:
    """Quick keyword filter to reduce noise before LLM review."""
    lower = title.lower()
    return any(kw in lower for kw in _AI_KEYWORDS)


def _fetch_hn_items(limit: int = 30) -> list[RawIntakeItem]:
    """Fetch top HN stories and filter for AI relevance."""
    items: list[RawIntakeItem] = []
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(HN_TOP_STORIES_URL)
            resp.raise_for_status()
            top_ids = resp.json()[:limit]

            for item_id in top_ids:
                try:
                    r = client.get(HN_ITEM_URL.format(item_id))
                    r.raise_for_status()
                    data = r.json()
                    if not data or data.get("type") != "story":
                        continue
                    title = data.get("title", "")
                    if not _is_ai_relevant(title):
                        continue
                    url = data.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
                    items.append(
                        RawIntakeItem(
                            source="hn",
                            source_url=url,
                            source_title=title,
                            raw_data=data,
                            discovered_at=datetime.now(timezone.utc),
                        )
                    )
                except Exception as exc:
                    log.warning("HN item %s fetch failed: %s", item_id, exc)
                    continue
    except Exception as exc:
        log.error("HN top stories fetch failed: %s", exc)
    log.info("Fetched %d AI-relevant HN items", len(items))
    return items


def _parse_rss_feed(source: str, xml_text: str) -> list[RawIntakeItem]:
    """Parse RSS/Atom XML into raw intake items."""
    items: list[RawIntakeItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("RSS parse error for %s: %s", source, exc)
        return items

    # Handle both RSS <channel><item> and Atom <feed><entry>
    channel = root.find("channel")
    if channel is not None:
        entries = channel.findall("item")
    else:
        # Atom namespace handling — try without namespace first
        entries = root.findall("entry")
        if not entries:
            # Strip namespace and retry
            ns_strip = xml_text.replace('xmlns="http://www.w3.org/2005/Atom"', "")
            try:
                root = ET.fromstring(ns_strip)
                entries = root.findall("entry")
            except ET.ParseError:
                entries = []

    for entry in entries:
        title_el = entry.find("title")
        title = title_el.text if title_el is not None else ""
        if not title or not _is_ai_relevant(title):
            continue

        link_el = entry.find("link")
        url = ""
        if link_el is not None:
            url = link_el.text or link_el.get("href", "")

        # Try to extract published date
        pub_el = entry.find("pubDate") or entry.find("published") or entry.find("updated")
        pub_str = pub_el.text if pub_el is not None else None
        discovered = datetime.now(timezone.utc)
        if pub_str:
            try:
                # RFC 2822 or ISO 8601 — try a few common formats
                from email.utils import parsedate_to_datetime

                discovered = parsedate_to_datetime(pub_str)
            except Exception:
                pass

        items.append(
            RawIntakeItem(
                source=source,
                source_url=url,
                source_title=title,
                raw_data={"pub_date": pub_str},
                discovered_at=discovered,
            )
        )
    return items


def _fetch_rss_source(name: str, url: str) -> list[RawIntakeItem]:
    """Fetch a single RSS feed."""
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "threads-analytics-intake/1.0"})
            resp.raise_for_status()
            items = _parse_rss_feed(name, resp.text)
            log.info("Fetched %d items from %s RSS", len(items), name)
            return items
    except Exception as exc:
        log.warning("RSS fetch failed for %s (%s): %s", name, url, exc)
        return []


def _fetch_rss_items(sources: dict[str, str] | None = None) -> list[RawIntakeItem]:
    """Fetch all configured RSS feeds."""
    if sources is None:
        sources = RSS_SOURCES
    items: list[RawIntakeItem] = []
    for name, url in sources.items():
        items.extend(_fetch_rss_source(name, url))
    # Also try fallbacks, swallowing errors
    for name, url in FALLBACK_RSS_SOURCES.items():
        items.extend(_fetch_rss_source(name, url))
    return items


def fetch_all_sources(
    *, hn_limit: int = 30, rss_sources: dict[str, str] | None = None
) -> list[RawIntakeItem]:
    """Fetch from all intake sources and return raw items."""
    hn_items = _fetch_hn_items(limit=hn_limit)
    rss_items = _fetch_rss_items(sources=rss_sources)
    return hn_items + rss_items
