"""LLM-based intake filter: score relevance, summarize, tag mechanics."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..llm_client import get_llm_client
from .fetchers import RawIntakeItem

log = logging.getLogger(__name__)

_INTAKE_SYSTEM_PROMPT = """You are a content intake filter for an AI operator in Indonesia who runs a BPO data pipeline business. The operator posts on Threads about AI tools, model comparisons, token costs, team adaptation, and recruitment — from a hands-on operator perspective (not theorizing).

For each input item, return a JSON object with these exact keys:
- "summary": string, 1–2 sentence English summary
- "operator_standing_score": integer 0–100
- "reasoning": string, 1 sentence why this is or isn't relevant
- "candidate_mechanics": list of strings, chosen from ["binary_verdict", "community_ask", "teaser_drop", "structured_finding", "mid_experiment", "token_receipt"]
- "relevance": string, one of "high", "medium", "low", "skip"

Skip items about consumer AI products, AI policy debates, or AI ethics unless they have direct operational implications. Prefer items about: model releases, pricing changes, developer tools, tokenomics, agent frameworks, enterprise AI adoption patterns, recruitment/hiring in AI.

Output MUST be a single JSON object (not markdown, no code fences)."""


@dataclass
class FilteredItem:
    raw: RawIntakeItem
    summary: str
    operator_standing_score: int
    reasoning: str
    candidate_mechanics: list[str]
    relevance: str


def _build_batch_prompt(items: list[RawIntakeItem]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"[{i}] Source: {item.source}")
        lines.append(f"Title: {item.source_title}")
        lines.append(f"URL: {item.source_url}")
        lines.append("")
    return "\n".join(lines)


def _parse_filter_response(text: str, items: list[RawIntakeItem]) -> list[FilteredItem]:
    """Parse LLM response into filtered items."""
    filtered: list[FilteredItem] = []

    # Try to find JSON array or object
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # If response is a single object, wrap it
    data: list[dict] | dict
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = [parsed]
        else:
            data = parsed
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse LLM filter response as JSON: %s", exc)
        # Fallback: treat every item as low relevance
        for item in items:
            filtered.append(
                FilteredItem(
                    raw=item,
                    summary=item.source_title,
                    operator_standing_score=50,
                    reasoning="Parse error — defaulting to medium relevance",
                    candidate_mechanics=[],
                    relevance="medium",
                )
            )
        return filtered

    for i, item in enumerate(items):
        entry = data[i] if i < len(data) else {}
        filtered.append(
            FilteredItem(
                raw=item,
                summary=entry.get("summary", item.source_title) or item.source_title,
                operator_standing_score=_clamp(
                    entry.get("operator_standing_score"), 0, 100
                ),
                reasoning=entry.get("reasoning", ""),
                candidate_mechanics=_clean_mechanics(entry.get("candidate_mechanics", [])),
                relevance=_clean_relevance(entry.get("relevance", "medium")),
            )
        )
    return filtered


def _clamp(val, low, high):
    if val is None:
        return 50
    try:
        return max(low, min(high, int(val)))
    except (ValueError, TypeError):
        return 50


def _clean_mechanics(mechanics) -> list[str]:
    valid = {
        "binary_verdict",
        "community_ask",
        "teaser_drop",
        "structured_finding",
        "mid_experiment",
        "token_receipt",
        "signal",
    }
    if not mechanics:
        return []
    return [m for m in mechanics if m in valid][:2]


def _clean_relevance(rel: str) -> str:
    if rel in ("high", "medium", "low", "skip"):
        return rel
    return "medium"


def filter_and_summarize_with_llm(items: list[RawIntakeItem]) -> list[FilteredItem]:
    """Send batch of items to LLM for relevance filtering and summarization."""
    if not items:
        return []

    client = get_llm_client()
    prompt = _build_batch_prompt(items)

    try:
        resp = client.create_message(
            max_tokens=4096,
            system=_INTAKE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        log.info("Intake filter LLM call: %s tokens in / %s tokens out", 
                 resp.usage.get("input_tokens") if resp.usage else "?",
                 resp.usage.get("output_tokens") if resp.usage else "?")
        return _parse_filter_response(resp.text, items)
    except Exception as exc:
        log.error("Intake filter LLM call failed: %s", exc)
        # Graceful degradation: return all items as medium relevance
        return [
            FilteredItem(
                raw=item,
                summary=item.source_title,
                operator_standing_score=50,
                reasoning="LLM filter failed — defaulting to medium relevance",
                candidate_mechanics=[],
                relevance="medium",
            )
            for item in items
        ]
