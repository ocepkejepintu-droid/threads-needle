"""Lead search orchestration — runs keyword searches across all active lead sources."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

from .db import session_scope
from .leads import create_lead_from_post
from .models import LeadSearchLog, LeadSource, Run

if TYPE_CHECKING:
    from .threads_client import ThreadsClient

log = logging.getLogger(__name__)


def run_lead_searches(run: Run, client: "ThreadsClient") -> dict:
    """Run lead discovery searches for all active lead sources.

    Iterates through active LeadSource configurations, searches for posts
    matching their keywords, and creates new Lead records for matches.

    Args:
        run: The current Run instance for tracking
        client: ThreadsClient instance for API calls

    Returns:
        Summary dict with counts:
        {
            "sources_searched": int,
            "posts_found": int,
            "leads_created": int,
            "errors": list[str],
        }
    """
    # Get current user's user_id from the API
    try:
        profile_data = client.get_me()
        your_user_id = str(profile_data.get("id") or "")
    except Exception as exc:
        log.error("Failed to get user profile: %s", exc)
        return {
            "sources_searched": 0,
            "posts_found": 0,
            "leads_created": 0,
            "errors": [f"Failed to get user profile: {exc}"],
        }

    result = {
        "sources_searched": 0,
        "posts_found": 0,
        "leads_created": 0,
        "errors": [],
    }

    with session_scope() as session:
        # Get all active lead sources
        sources = session.scalars(
            select(LeadSource).where(LeadSource.is_active.is_(True))
        ).all()

        for source in sources:
            try:
                source_result = _search_single_source(
                    session=session,
                    source=source,
                    client=client,
                    run=run,
                    your_user_id=your_user_id,
                )
                result["sources_searched"] += 1
                result["posts_found"] += source_result["posts_found"]
                result["leads_created"] += source_result["leads_created"]
            except Exception as exc:
                error_msg = f"Source '{source.name}': {exc}"
                result["errors"].append(error_msg)
                log.error("Lead search failed for source %s: %s", source.name, exc)

                # Still create a search log for the error
                log_entry = LeadSearchLog(
                    source_id=source.id,
                    run_id=run.id,
                    keywords_searched=list(source.keywords),
                    posts_found=0,
                    leads_created=0,
                    error_message=str(exc),
                )
                session.add(log_entry)

    return result


def _search_single_source(
    session,
    source: LeadSource,
    client: "ThreadsClient",
    run: Run,
    your_user_id: str,
) -> dict:
    """Search for leads from a single LeadSource.

    Args:
        session: Database session
        source: The LeadSource to search
        client: ThreadsClient instance
        run: The current Run instance
        your_user_id: Current user's user ID (to skip own posts)

    Returns:
        Dict with posts_found and leads_created counts for this source
    """
    posts_found = 0
    leads_created = 0

    for keyword in source.keywords:
        try:
            results = client.keyword_search(query=keyword, limit=25)
            posts_found += len(results)

            for result in results:
                # Convert SearchResult to post dict format expected by create_lead_from_post
                post = {
                    "id": result.post.id,
                    "text": result.post.text,
                    "username": result.post.username,
                    "user_id": result.author_user_id,
                    "permalink": result.post.permalink,
                    "timestamp": result.post.created_at.isoformat() if result.post.created_at else None,
                    "reply_count": result.insight.replies if result.insight else 0,
                    "owner": {
                        "id": result.author_user_id,
                        "biography": None,  # Not available from search results
                    },
                }

                # Skip logic is handled inside create_lead_from_post
                lead = create_lead_from_post(
                    session=session,
                    source=source,
                    post=post,
                    matched_keyword=keyword,
                    your_user_id=your_user_id,
                )

                if lead:
                    leads_created += 1

        except Exception as exc:
            log.warning("Keyword search failed for '%s' in source '%s': %s", keyword, source.name, exc)
            continue

    # Update source last searched timestamp
    source.last_searched_at = datetime.now(timezone.utc)

    # Create search log entry
    log_entry = LeadSearchLog(
        source_id=source.id,
        run_id=run.id,
        keywords_searched=list(source.keywords),
        posts_found=posts_found,
        leads_created=leads_created,
    )
    session.add(log_entry)

    log.info(
        "Source '%s': found %d posts, created %d leads",
        source.name,
        posts_found,
        leads_created,
    )

    return {
        "posts_found": posts_found,
        "leads_created": leads_created,
    }
