"""Lead discovery and search functions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from .models import Lead, LeadSearchLog, LeadSource

log = logging.getLogger(__name__)


def run_lead_searches(session) -> dict:
    """Run lead discovery searches for all active lead sources.
    
    Iterates through active LeadSource configurations, searches for posts
    matching their keywords, and creates new Lead records for matches.
    
    Returns a summary dict with counts of posts found and leads created.
    """
    from .threads_client import ThreadsClient
    
    # Import inside function to avoid circular imports at module level
    client = ThreadsClient()
    
    total_posts_found = 0
    total_leads_created = 0
    errors = []
    
    # Get all active lead sources
    sources = session.scalars(
        select(LeadSource).where(LeadSource.is_active == True)
    ).all()
    
    for source in sources:
        posts_found = 0
        leads_created = 0
        error_msg = None
        
        try:
            for keyword in source.keywords:
                try:
                    results = client.keyword_search(query=keyword, limit=25)
                    posts_found += len(results)
                    
                    for result in results:
                        # Check if lead already exists for this author+post
                        existing = session.scalar(
                            select(Lead).where(
                                Lead.thread_id == result.post.id
                            )
                        )
                        if existing:
                            continue
                            
                        # Create new lead
                        lead = Lead(
                            source_id=source.id,
                            thread_id=result.post.id,
                            author_username=result.post.username or "unknown",
                            author_user_id=result.author_user_id or "",
                            post_text=result.post.text,
                            post_permalink=result.post.permalink or "",
                            post_created_at=result.post.created_at,
                            matched_keyword=keyword,
                            status="new",
                        )
                        session.add(lead)
                        leads_created += 1
                        
                except Exception as exc:
                    log.warning("Keyword search failed for '%s': %s", keyword, exc)
                    continue
                    
        except Exception as exc:
            error_msg = str(exc)
            errors.append(f"Source '{source.name}': {error_msg}")
            log.error("Lead search failed for source %s: %s", source.name, exc)
        
        # Update source last searched timestamp
        source.last_searched_at = datetime.now(timezone.utc)
        
        # Create search log
        log_entry = LeadSearchLog(
            source_id=source.id,
            keywords_searched=list(source.keywords),
            posts_found=posts_found,
            leads_created=leads_created,
            error_message=error_msg,
        )
        session.add(log_entry)
        
        total_posts_found += posts_found
        total_leads_created += leads_created
    
    session.commit()
    
    return {
        "posts_found": total_posts_found,
        "leads_created": total_leads_created,
        "sources_checked": len(sources),
        "errors": errors,
    }
