"""Orchestration: fetch → dedupe → filter → persist intake items."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..db import session_scope
from ..models import IntakeItem
from .dedupe import dedupe_items
from .fetchers import RawIntakeItem, fetch_all_sources
from .filter import FilteredItem, filter_and_summarize_with_llm

log = logging.getLogger(__name__)


def persist_filtered_items(
    filtered: list[FilteredItem], account_id: int = 1
) -> list[IntakeItem]:
    """Write filtered items to DB, skipping duplicates by URL."""
    created: list[IntakeItem] = []
    with session_scope() as session:
        # Build set of existing URLs for this account
        existing = {
            row[0]
            for row in session.execute(
                select(IntakeItem.source_url).where(IntakeItem.account_id == account_id)
            )
        }

        for f in filtered:
            if f.relevance == "skip":
                continue
            if f.raw.source_url in existing:
                continue

            item = IntakeItem(
                account_id=account_id,
                source=f.raw.source,
                source_url=f.raw.source_url,
                source_title=f.raw.source_title,
                raw_data=f.raw.raw_data,
                summary=f.summary,
                operator_standing_score=f.operator_standing_score,
                candidate_mechanics=f.candidate_mechanics,
                relevance=f.relevance,
                status="new",
            )
            session.add(item)
            created.append(item)
            existing.add(f.raw.source_url)

    log.info("Persisted %d new intake items (skipped %d duplicates/skip-rated)",
             len(created),
             len(filtered) - len(created))
    return created


def run_intake_cycle(
    *, account_id: int = 1, hn_limit: int = 30
) -> dict[str, int | list[str]]:
    """Full intake cycle: fetch all sources, dedupe, filter with LLM, persist."""
    log.info("Starting intake cycle for account %s", account_id)

    raw = fetch_all_sources(hn_limit=hn_limit)
    if not raw:
        log.warning("No raw items fetched from any source")
        return {"fetched": 0, "deduped": 0, "persisted": 0, "sources": []}

    deduped = dedupe_items(raw)
    filtered = filter_and_summarize_with_llm(deduped)
    created = persist_filtered_items(filtered, account_id=account_id)

    return {
        "fetched": len(raw),
        "deduped": len(deduped),
        "persisted": len(created),
        "sources": list({r.source for r in raw}),
    }


# Import select at module bottom to avoid circular issues with SQLAlchemy

def expire_old_items(account_id: int = 1) -> int:
    """Mark intake items older than 7 days as expired."""
    from sqlalchemy import update

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with session_scope() as session:
        result = session.execute(
            update(IntakeItem)
            .where(IntakeItem.account_id == account_id)
            .where(IntakeItem.status == "new")
            .where(IntakeItem.expires_at <= cutoff)
            .values(status="expired")
        )
        count = result.rowcount
        if count:
            log.info("Expired %d intake items", count)
        return count


from sqlalchemy import select  # noqa: E402
