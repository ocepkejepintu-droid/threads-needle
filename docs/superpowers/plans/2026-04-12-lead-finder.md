# Lead Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lead discovery and reply system that searches Threads for pain points, presents them in a Kanban queue with AI-drafted helpful replies, and enables human-in-the-loop approval before sending.

**Architecture:** Three new database models (LeadSource, Lead, LeadSearchLog) support a daily search workflow. New leads module handles business logic, leads_search.py orchestrates searches. Web UI adds Kanban queue, detail page, and keyword management. All AI drafts use existing LLMClient with OpenRouter/Claude.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, SQLite, Jinja2, existing LLMClient

---

## File Structure

| File | Purpose |
|------|---------|
| `src/threads_analytics/models.py` (modify) | Add LeadSource, Lead, LeadSearchLog ORM models |
| `src/threads_analytics/leads.py` (create) | Core business logic: search, generate drafts, send replies |
| `src/threads_analytics/leads_search.py` (create) | Daily search orchestration, deduplication |
| `src/threads_analytics/pipeline.py` (modify) | Add lead search step to pipeline |
| `src/threads_analytics/cli.py` (modify) | Add search-leads and leads-stats commands |
| `src/threads_analytics/web/routes.py` (modify) | Add all lead-related routes |
| `src/threads_analytics/web/templates/leads.html` (create) | Kanban queue page |
| `src/threads_analytics/web/templates/lead_detail.html` (create) | Lead review/edit page |
| `src/threads_analytics/web/templates/lead_sources.html` (create) | Keyword sources list |
| `src/threads_analytics/web/templates/lead_source_form.html` (create) | Create/edit source form |
| `src/threads_analytics/web/templates/base.html` (modify) | Add sidebar nav item |
| `tests/test_leads.py` (create) | Unit tests for leads module |

---

## Prerequisites

Ensure these are in place before starting:
- `.env` has `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
- Database migrations are handled automatically by SQLAlchemy (no Alembic needed)
- Existing `threads_client.py` has keyword search capability

---

## Task 1: Database Models

**Files:**
- Create: None (modify existing)
- Modify: `src/threads_analytics/models.py`

### Step 1: Add LeadSource model

Add after existing models, following the same pattern as Experiment:

```python
class LeadSource(Base):
    """Configuration for lead search keywords."""
    __tablename__ = "lead_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(default=True)
    search_frequency_hours: Mapped[int] = mapped_column(default=24)
    last_searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

### Step 2: Add Lead model

```python
class Lead(Base):
    """Discovered lead opportunity from keyword search."""
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("lead_sources.id"))
    
    # Thread/Post info
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_username: Mapped[str] = mapped_column(String(256), nullable=False)
    author_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_bio: Mapped[str | None] = mapped_column(Text)
    post_text: Mapped[str] = mapped_column(Text, nullable=False)
    post_permalink: Mapped[str] = mapped_column(String(512))
    post_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    
    # Matching
    matched_keyword: Mapped[str] = mapped_column(String(256), nullable=False)
    
    # Workflow status: new, reviewed, approved, sent, rejected
    status: Mapped[str] = mapped_column(String(32), default="new")
    
    # AI-generated content
    ai_draft_reply: Mapped[str | None] = mapped_column(Text)
    ai_draft_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Final content (editable by user)
    final_reply: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Relationship
    source: Mapped["LeadSource"] = relationship("LeadSource", back_populates="leads")
    
    __table_args__ = (
        # Prevent re-contacting same person same day
        UniqueConstraint('author_user_id', 'created_at', name='uq_lead_author_date'),
    )
```

Also add to LeadSource:
```python
class LeadSource(Base):
    # ... existing fields ...
    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="source")
```

### Step 3: Add LeadSearchLog model

```python
class LeadSearchLog(Base):
    """Audit log for lead searches."""
    __tablename__ = "lead_search_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("lead_sources.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    keywords_searched: Mapped[list[str]] = mapped_column(JSON, default=list)
    posts_found: Mapped[int] = mapped_column(default=0)
    leads_created: Mapped[int] = mapped_column(default=0)
    searched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    error_message: Mapped[str | None] = mapped_column(Text)
```

### Step 4: Verify models compile

Run: `python -c "from threads_analytics.models import LeadSource, Lead, LeadSearchLog; print('Models OK')"`
Expected: `Models OK`

### Step 5: Commit

```bash
git add src/threads_analytics/models.py
git commit -m "feat(leads): add LeadSource, Lead, LeadSearchLog models"
```

---

## Task 2: Leads Business Logic Core

**Files:**
- Create: `src/threads_analytics/leads.py`

### Step 1: Create file with imports and constants

```python
"""Lead discovery and reply business logic."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .llm_client import create_llm_client
from .models import Lead, LeadSearchLog, LeadSource, MyPost

log = logging.getLogger(__name__)

# Maximum replies per day (configurable)
MAX_DAILY_REPLIES = 10

# Cooldown period before re-contacting same user
RECONTACT_COOLDOWN_DAYS = 30
```

### Step 2: Add should_skip_post function

```python
def should_skip_post(
    post_text: str,
    author_user_id: str,
    your_user_id: str,
    reply_count: int = 0,
) -> tuple[bool, str]:
    """Determine if a post should be skipped as a lead.
    
    Returns: (should_skip, reason)
    """
    # Skip your own posts
    if author_user_id == your_user_id:
        return True, "own_post"
    
    # Skip if too many replies (too noisy)
    if reply_count > 10:
        return True, "too_many_replies"
    
    # Skip very short posts (not enough context)
    if len(post_text.strip()) < 20:
        return True, "too_short"
    
    # Skip if clearly a rant (heuristic: excessive punctuation/caps)
    caps_ratio = sum(1 for c in post_text if c.isupper()) / max(len(post_text), 1)
    if caps_ratio > 0.5:
        return True, "excessive_caps"
    
    return False, ""
```

### Step 3: Add generate_reply_draft function

```python
SYSTEM_PROMPT = """You are an automation expert helping someone with a specific business problem.

Reply guidelines:
1. Acknowledge their specific pain point (show you read it)
2. Share one genuinely helpful tip or insight (2-3 sentences)
3. Soft CTA: "I help businesses automate this. DM me if you want to chat" (ONLY if relevant)
4. Keep it under 280 characters
5. Never sound salesy. Sound like a helpful peer

Generate a reply that follows these guidelines."""


def generate_reply_draft(
    post_text: str,
    author_bio: str | None,
    matched_keyword: str,
) -> str | None:
    """Generate AI reply draft using configured LLM."""
    settings = get_settings()
    
    try:
        client = create_llm_client()
    except ValueError as e:
        log.warning("LLM client not configured: %s", e)
        return None
    
    user_content = f"""Matched keyword: {matched_keyword}

Author bio: {author_bio or "N/A"}

Post content:
{post_text}

Generate a helpful reply (max 280 chars):"""
    
    try:
        resp = client.create_message(
            model=settings.openrouter_model if settings.llm_provider == "openrouter" else settings.claude_recommender_model,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            temperature=0.7,
        )
        return resp.text.strip()[:280]  # Enforce limit
    except Exception as e:
        log.warning("Failed to generate reply draft: %s", e)
        return None
```

### Step 4: Add send_reply function

```python
def send_reply(
    session: Session,
    lead: Lead,
    client: Any,  # ThreadsClient
) -> bool:
    """Send approved reply to Threads.
    
    Returns True if successful, False otherwise.
    """
    from .threads_client import ThreadsClient
    
    if lead.status not in ("approved", "sent"):
        log.warning("Cannot send lead %d: status=%s", lead.id, lead.status)
        return False
    
    reply_text = lead.final_reply or lead.ai_draft_reply
    if not reply_text:
        log.warning("No reply text for lead %d", lead.id)
        return False
    
    # Check daily limit
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = session.scalar(
        select(func.count()).select_from(Lead).where(
            and_(
                Lead.status == "sent",
                Lead.sent_at >= today_start,
            )
        )
    ) or 0
    
    if sent_today >= MAX_DAILY_REPLIES:
        log.warning("Daily reply limit reached (%d)", MAX_DAILY_REPLIES)
        return False
    
    try:
        # Post reply via API
        result = client.post_reply(
            thread_id=lead.thread_id,
            text=reply_text,
        )
        
        lead.status = "sent"
        lead.sent_at = datetime.now(timezone.utc)
        log.info("Sent reply to lead %d (thread %s)", lead.id, lead.thread_id)
        return True
        
    except Exception as e:
        log.error("Failed to send reply for lead %d: %s", lead.id, e)
        return False
```

### Step 5: Add create_lead_from_post function

```python
def create_lead_from_post(
    session: Session,
    source: LeadSource,
    post: dict[str, Any],
    matched_keyword: str,
    your_user_id: str,
) -> Lead | None:
    """Create Lead from API post data if not duplicate.
    
    Returns Lead if created, None if skipped/duplicate.
    """
    author_user_id = post.get("user_id") or post.get("username", "")
    
    # Check should skip
    skip, reason = should_skip_post(
        post_text=post.get("text", ""),
        author_user_id=author_user_id,
        your_user_id=your_user_id,
        reply_count=post.get("reply_count", 0),
    )
    if skip:
        log.debug("Skipping post %s: %s", post.get("id"), reason)
        return None
    
    # Check for recent contact (cooldown)
    cooldown_date = datetime.now(timezone.utc) - timedelta(days=RECONTACT_COOLDOWN_DAYS)
    recent_contact = session.scalar(
        select(Lead).where(
            and_(
                Lead.author_user_id == author_user_id,
                Lead.created_at >= cooldown_date,
            )
        )
    )
    if recent_contact:
        log.debug("Skipping %s: contacted within %d days", author_user_id, RECONTACT_COOLDOWN_DAYS)
        return None
    
    # Check for duplicate today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    duplicate_today = session.scalar(
        select(Lead).where(
            and_(
                Lead.author_user_id == author_user_id,
                Lead.created_at >= today_start,
            )
        )
    )
    if duplicate_today:
        log.debug("Skipping %s: already in queue today", author_user_id)
        return None
    
    # Generate AI draft
    ai_draft = generate_reply_draft(
        post_text=post.get("text", ""),
        author_bio=post.get("user", {}).get("biography"),
        matched_keyword=matched_keyword,
    )
    
    lead = Lead(
        source_id=source.id,
        thread_id=post.get("id"),
        author_username=post.get("username", ""),
        author_user_id=author_user_id,
        author_bio=post.get("user", {}).get("biography"),
        post_text=post.get("text", ""),
        post_permalink=post.get("permalink", ""),
        post_created_at=post.get("timestamp"),
        matched_keyword=matched_keyword,
        status="new",
        ai_draft_reply=ai_draft,
        ai_draft_generated_at=datetime.now(timezone.utc) if ai_draft else None,
    )
    session.add(lead)
    log.info("Created lead %d from %s", lead.id, lead.author_username)
    return lead
```

### Step 6: Verify file compiles

Run: `python -c "from threads_analytics.leads import generate_reply_draft, send_reply, create_lead_from_post; print('leads.py OK')"`
Expected: `leads.py OK`

### Step 7: Commit

```bash
git add src/threads_analytics/leads.py
git commit -m "feat(leads): add business logic for reply generation and sending"
```

---

## Task 3: Lead Search Orchestration

**Files:**
- Create: `src/threads_analytics/leads_search.py`

### Step 1: Create file with search orchestration

```python
"""Lead search orchestration - runs daily searches across all sources."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session_scope
from .leads import create_lead_from_post
from .models import LeadSearchLog, LeadSource, Run
from .threads_client import ThreadsClient

log = logging.getLogger(__name__)


def run_lead_searches(
    run: Run | None = None,
    client: ThreadsClient | None = None,
) -> dict[str, Any]:
    """Run searches for all active LeadSources.
    
    Called by pipeline or manually via CLI.
    Returns summary dict for logging.
    """
    summary = {
        "sources_searched": 0,
        "posts_found": 0,
        "leads_created": 0,
        "errors": [],
    }
    
    with session_scope() as session:
        # Get active sources that need searching
        sources = session.scalars(
            select(LeadSource).where(LeadSource.is_active == True)
        ).all()
        
        if not sources:
            log.info("No active lead sources to search")
            return summary
        
        # Get your user ID for skip logic
        your_profile = session.scalar(select(MyProfile))
        your_user_id = your_profile.user_id if your_profile else ""
        
        with ThreadsClient() as client:
            for source in sources:
                try:
                    result = _search_single_source(
                        session, source, client, run, your_user_id
                    )
                    summary["sources_searched"] += 1
                    summary["posts_found"] += result["posts_found"]
                    summary["leads_created"] += result["leads_created"]
                except Exception as e:
                    log.exception("Failed to search source %d: %s", source.id, e)
                    summary["errors"].append(f"source_{source.id}: {e}")
    
    return summary


def _search_single_source(
    session: Session,
    source: LeadSource,
    client: ThreadsClient,
    run: Run | None,
    your_user_id: str,
) -> dict[str, int]:
    """Search a single LeadSource."""
    result = {"posts_found": 0, "leads_created": 0}
    
    log.info("Searching source '%s' with %d keywords", source.name, len(source.keywords))
    
    search_log = LeadSearchLog(
        source_id=source.id,
        run_id=run.id if run else None,
        keywords_searched=source.keywords,
    )
    session.add(search_log)
    
    for keyword in source.keywords:
        try:
            posts = client.keyword_search(keyword, limit=25)
            result["posts_found"] += len(posts)
            
            for post in posts:
                lead = create_lead_from_post(
                    session=session,
                    source=source,
                    post=post,
                    matched_keyword=keyword,
                    your_user_id=your_user_id,
                )
                if lead:
                    result["leads_created"] += 1
                    
        except Exception as e:
            log.warning("Keyword search failed for '%s': %s", keyword, e)
            continue
    
    # Update source timestamp
    source.last_searched_at = datetime.now(timezone.utc)
    
    # Update search log
    search_log.posts_found = result["posts_found"]
    search_log.leads_created = result["leads_created"]
    
    log.info(
        "Source '%s' complete: %d posts, %d leads",
        source.name,
        result["posts_found"],
        result["leads_created"],
    )
    
    return result
```

### Step 2: Add import for MyProfile

Add to imports:
```python
from .models import LeadSearchLog, LeadSource, MyProfile, Run
```

### Step 3: Verify file compiles

Run: `python -c "from threads_analytics.leads_search import run_lead_searches; print('leads_search.py OK')"`
Expected: `leads_search.py OK`

### Step 4: Commit

```bash
git add src/threads_analytics/leads_search.py
git commit -m "feat(leads): add search orchestration module"
```

---

## Task 4: Integrate into Pipeline

**Files:**
- Modify: `src/threads_analytics/pipeline.py`

### Step 1: Add import

Add to imports:
```python
from .leads_search import run_lead_searches
```

### Step 2: Add lead search step

After the topics extraction step (~line 53), add:

```python
            # 2b. Lead discovery
            try:
                with session_scope() as session:
                    run = session.get(Run, run_id)
                    summary["leads"] = run_lead_searches(run, client)
            except Exception as exc:
                log.warning("Lead search failed: %s", exc)
                summary["leads_error"] = repr(exc)
```

### Step 3: Verify pipeline imports work

Run: `python -c "from threads_analytics.pipeline import run_full_cycle; print('pipeline.py OK')"`
Expected: `pipeline.py OK`

### Step 4: Commit

```bash
git add src/threads_analytics/pipeline.py
git commit -m "feat(leads): integrate lead search into pipeline"
```

---

## Task 5: CLI Commands

**Files:**
- Modify: `src/threads_analytics/cli.py`

### Step 1: Add imports

```python
from .db import session_scope
from .leads_search import run_lead_searches
from .models import Lead
from sqlalchemy import func, select
from datetime import datetime, timezone
```

### Step 2: Add search-leads command

After the existing `whoami` command, add:

```python
@app.command()
def search_leads(
    manual: bool = typer.Option(False, "--manual", "-m", help="Run even if not due by frequency"),
):
    """Manually trigger lead search across all active sources."""
    from .threads_client import ThreadsClient
    
    if manual:
        typer.echo("Running manual lead search...")
    else:
        typer.echo("Checking lead sources...")
    
    summary = run_lead_searches()
    
    typer.echo(f"Sources searched: {summary['sources_searched']}")
    typer.echo(f"Posts found: {summary['posts_found']}")
    typer.echo(f"Leads created: {summary['leads_created']}")
    
    if summary['errors']:
        typer.echo(f"Errors: {len(summary['errors'])}", err=True)
```

### Step 3: Add leads-stats command

```python
@app.command()
def leads_stats():
    """Show lead queue statistics."""
    from .models import LeadSource
    
    with session_scope() as session:
        # Count by status
        status_counts = {}
        for status in ["new", "reviewed", "approved", "sent", "rejected"]:
            count = session.scalar(
                select(func.count()).select_from(Lead).where(Lead.status == status)
            ) or 0
            status_counts[status] = count
        
        # Daily replies sent
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = session.scalar(
            select(func.count()).select_from(Lead).where(
                and_(Lead.status == "sent", Lead.sent_at >= today_start)
            )
        ) or 0
        
        # Active sources
        active_sources = session.scalar(
            select(func.count()).select_from(LeadSource).where(LeadSource.is_active == True)
        ) or 0
        
        typer.echo("Lead Finder Statistics")
        typer.echo("=" * 40)
        typer.echo(f"Active sources: {active_sources}")
        typer.echo("")
        typer.echo("Queue status:")
        typer.echo(f"  New: {status_counts['new']}")
        typer.echo(f"  Reviewed: {status_counts['reviewed']}")
        typer.echo(f"  Approved: {status_counts['approved']}")
        typer.echo(f"  Sent: {status_counts['sent']}")
        typer.echo(f"  Rejected: {status_counts['rejected']}")
        typer.echo("")
        typer.echo(f"Replies sent today: {sent_today}/10")
```

### Step 4: Verify CLI works

Run: `source .venv/bin/activate && threads-analytics leads-stats`
Expected: Shows statistics (all zeros initially)

Run: `threads-analytics search-leads --manual`
Expected: Runs search (may find 0 if no sources configured yet)

### Step 5: Commit

```bash
git add src/threads_analytics/cli.py
git commit -m "feat(leads): add search-leads and leads-stats CLI commands"
```

---

## Task 6: Web Routes

**Files:**
- Modify: `src/threads_analytics/web/routes.py`

### Step 1: Add imports

```python
from ..leads import generate_reply_draft, send_reply
from ..leads_search import run_lead_searches
from ..models import Lead, LeadSearchLog, LeadSource
```

### Step 2: Add helper functions

Add after `_recent_runs` function:

```python
def _leads_by_status(session, status: str, limit: int = 50) -> list[dict]:
    """Get leads filtered by status."""
    leads = session.scalars(
        select(Lead)
        .where(Lead.status == status)
        .order_by(desc(Lead.created_at))
        .limit(limit)
    ).all()
    
    return [
        {
            "id": l.id,
            "author_username": l.author_username,
            "author_bio": l.author_bio,
            "post_text": l.post_text[:200] + "..." if len(l.post_text) > 200 else l.post_text,
            "post_permalink": l.post_permalink,
            "matched_keyword": l.matched_keyword,
            "status": l.status,
            "created_at": l.created_at,
            "ai_draft_reply": l.ai_draft_reply,
        }
        for l in leads
    ]
```

### Step 3: Add route: GET /leads (Kanban)

Inside `build_router`, add after the existing routes:

```python
    @router.get("/leads", response_class=HTMLResponse)
    def leads_index(request: Request) -> HTMLResponse:
        with session_scope() as session:
            new_leads = _leads_by_status(session, "new", limit=50)
            reviewed_leads = _leads_by_status(session, "reviewed", limit=20)
            approved_leads = _leads_by_status(session, "approved", limit=20)
            sent_leads = _leads_by_status(session, "sent", limit=20)
            
            # Daily reply count
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = session.scalar(
                select(func.count()).select_from(Lead).where(
                    and_(Lead.status == "sent", Lead.sent_at >= today_start)
                )
            ) or 0
            
            sources = session.scalars(select(LeadSource).where(LeadSource.is_active == True)).all()
        
        return templates.TemplateResponse(
            request,
            "leads.html",
            {
                "new_leads": new_leads,
                "reviewed_leads": reviewed_leads,
                "approved_leads": approved_leads,
                "sent_leads": sent_leads,
                "sent_today": sent_today,
                "daily_limit": 10,
                "active_sources": len(sources),
            },
        )
```

### Step 4: Add route: GET /leads/{id} (Detail)

```python
    @router.get("/leads/{lead_id}", response_class=HTMLResponse)
    def lead_detail(request: Request, lead_id: int) -> HTMLResponse:
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise HTTPException(404, "lead not found")
            
            # Daily reply count
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = session.scalar(
                select(func.count()).select_from(Lead).where(
                    and_(Lead.status == "sent", Lead.sent_at >= today_start)
                )
            ) or 0
            
            payload = {
                "id": lead.id,
                "author_username": lead.author_username,
                "author_bio": lead.author_bio,
                "post_text": lead.post_text,
                "post_permalink": lead.post_permalink,
                "post_created_at": lead.post_created_at,
                "matched_keyword": lead.matched_keyword,
                "status": lead.status,
                "ai_draft_reply": lead.ai_draft_reply,
                "final_reply": lead.final_reply,
                "notes": lead.notes,
                "created_at": lead.created_at,
            }
        
        return templates.TemplateResponse(
            request,
            "lead_detail.html",
            {
                "lead": payload,
                "sent_today": sent_today,
                "daily_limit": 10,
            },
        )
```

### Step 5: Add route: POST /leads/{id}/regenerate

```python
    @router.post("/leads/{lead_id}/regenerate")
    def lead_regenerate(lead_id: int) -> RedirectResponse:
        """Regenerate AI draft for a lead."""
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise HTTPException(404, "lead not found")
            
            new_draft = generate_reply_draft(
                post_text=lead.post_text,
                author_bio=lead.author_bio,
                matched_keyword=lead.matched_keyword,
            )
            
            if new_draft:
                lead.ai_draft_reply = new_draft
                lead.ai_draft_generated_at = datetime.now(timezone.utc)
        
        return RedirectResponse(f"/leads/{lead_id}", status_code=303)
```

### Step 6: Add route: POST /leads/{id}/save-draft

```python
    @router.post("/leads/{lead_id}/save-draft")
    def lead_save_draft(
        lead_id: int,
        final_reply: str = Form(""),
        notes: str = Form(""),
    ) -> RedirectResponse:
        """Save edited draft and mark as reviewed."""
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise HTTPException(404, "lead not found")
            
            lead.final_reply = final_reply[:280]  # Enforce limit
            lead.notes = notes
            lead.status = "reviewed"
            lead.reviewed_at = datetime.now(timezone.utc)
        
        return RedirectResponse(f"/leads/{lead_id}", status_code=303)
```

### Step 7: Add route: POST /leads/{id}/approve

```python
    @router.post("/leads/{lead_id}/approve")
    def lead_approve(
        lead_id: int,
        final_reply: str = Form(""),
    ) -> RedirectResponse:
        """Approve and send reply."""
        from ...threads_client import ThreadsClient
        
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise HTTPException(404, "lead not found")
            
            lead.final_reply = final_reply[:280]
            lead.status = "approved"
            lead.reviewed_at = datetime.now(timezone.utc)
            
            # Send reply
            try:
                with ThreadsClient() as client:
                    success = send_reply(session, lead, client)
                    if not success:
                        raise HTTPException(500, "failed to send reply")
            except Exception as e:
                log.exception("Failed to send reply for lead %d", lead_id)
                raise HTTPException(500, f"failed to send reply: {e}")
        
        return RedirectResponse("/leads", status_code=303)
```

### Step 8: Add route: POST /leads/{id}/reject

```python
    @router.post("/leads/{lead_id}/reject")
    def lead_reject(lead_id: int) -> RedirectResponse:
        """Reject a lead (not interested)."""
        with session_scope() as session:
            lead = session.get(Lead, lead_id)
            if lead is None:
                raise HTTPException(404, "lead not found")
            
            lead.status = "rejected"
            lead.rejected_at = datetime.now(timezone.utc)
        
        return RedirectResponse("/leads", status_code=303)
```

### Step 9: Add route: GET /leads/sources

```python
    @router.get("/leads/sources", response_class=HTMLResponse)
    def lead_sources(request: Request) -> HTMLResponse:
        with session_scope() as session:
            sources = session.scalars(
                select(LeadSource).order_by(desc(LeadSource.created_at))
            ).all()
            
            payload = [
                {
                    "id": s.id,
                    "name": s.name,
                    "keywords": s.keywords,
                    "is_active": s.is_active,
                    "last_searched_at": s.last_searched_at,
                }
                for s in sources
            ]
        
        return templates.TemplateResponse(
            request, "lead_sources.html", {"sources": payload}
        )
```

### Step 10: Add route: GET /leads/sources/new

```python
    @router.get("/leads/sources/new", response_class=HTMLResponse)
    def lead_source_new_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "lead_source_form.html", {"source": None})
```

### Step 11: Add route: POST /leads/sources

```python
    @router.post("/leads/sources")
    def lead_source_create(
        name: str = Form(...),
        keywords: str = Form(...),
        is_active: bool = Form(False),
    ) -> RedirectResponse:
        """Create new lead source."""
        keyword_list = [k.strip() for k in keywords.split("\n") if k.strip()]
        
        with session_scope() as session:
            source = LeadSource(
                name=name,
                keywords=keyword_list,
                is_active=is_active,
            )
            session.add(source)
        
        return RedirectResponse("/leads/sources", status_code=303)
```

### Step 12: Commit routes

```bash
git add src/threads_analytics/web/routes.py
git commit -m "feat(leads): add all web routes for lead management"
```

---

## Task 7: HTML Templates

### Step 1: Create leads.html (Kanban)

Create `src/threads_analytics/web/templates/leads.html`:

```html
{% extends "base.html" %}
{% block content %}

<div class="leads-header">
  <h1>Lead Finder</h1>
  <div class="leads-actions">
    <span class="daily-limit">{{ sent_today }}/{{ daily_limit }} replies today</span>
    <a href="/leads/sources" class="cta">Manage Sources ({{ active_sources }})</a>
  </div>
</div>

<div class="kanban-board">
  
  <div class="kanban-column" data-status="new">
    <h2>📥 New <span class="count">({{ new_leads|length }})</span></h2>
    <div class="kanban-cards">
      {% for lead in new_leads %}
      <a href="/leads/{{ lead.id }}" class="kanban-card">
        <div class="card-author">@{{ lead.author_username }}</div>
        <div class="card-preview">{{ lead.post_text }}</div>
        <div class="card-meta">
          <span class="keyword">{{ lead.matched_keyword }}</span>
          <span class="time">{{ lead.created_at.strftime("%H:%M") }}</span>
        </div>
      </a>
      {% endfor %}
    </div>
  </div>
  
  <div class="kanban-column" data-status="reviewed">
    <h2>👀 Reviewed <span class="count">({{ reviewed_leads|length }})</span></h2>
    <div class="kanban-cards">
      {% for lead in reviewed_leads %}
      <a href="/leads/{{ lead.id }}" class="kanban-card">
        <div class="card-author">@{{ lead.author_username }}</div>
        <div class="card-preview">{{ lead.post_text }}</div>
        <div class="card-meta">
          <span class="keyword">{{ lead.matched_keyword }}</span>
        </div>
      </a>
      {% endfor %}
    </div>
  </div>
  
  <div class="kanban-column" data-status="sent">
    <h2>✅ Sent <span class="count">({{ sent_leads|length }})</span></h2>
    <div class="kanban-cards">
      {% for lead in sent_leads %}
      <div class="kanban-card kanban-card--sent">
        <div class="card-author">@{{ lead.author_username }}</div>
        <div class="card-preview">{{ lead.post_text }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
  
</div>

{% endblock %}
```

### Step 2: Create lead_detail.html

Create `src/threads_analytics/web/templates/lead_detail.html`:

```html
{% extends "base.html" %}
{% block content %}

<a href="/leads" class="back-link">← Back to Leads</a>

<div class="lead-detail">
  
  <div class="lead-header">
    <h1>Lead from @{{ lead.author_username }}</h1>
    <div class="lead-actions">
      {% if lead.status != "sent" %}
      <form method="post" action="/leads/{{ lead.id }}/reject" style="display: inline;">
        <button type="submit" class="btn btn-secondary">Reject</button>
      </form>
      {% endif %}
    </div>
  </div>
  
  {% if lead.author_bio %}
  <div class="lead-bio">{{ lead.author_bio }}</div>
  {% endif %}
  
  <div class="lead-post">
    <h3>Original Post</h3>
    <blockquote>{{ lead.post_text }}</blockquote>
    <div class="post-meta">
      <a href="{{ lead.post_permalink }}" target="_blank">View on Threads →</a>
      <span>Matched: {{ lead.matched_keyword }}</span>
    </div>
  </div>
  
  <form method="post" action="/leads/{{ lead.id }}/save-draft" class="reply-form">
    <h3>Your Reply</h3>
    
    <textarea
      name="final_reply"
      id="reply-text"
      rows="4"
      maxlength="280"
      placeholder="AI will generate a draft, or write your own..."
    >{{ lead.final_reply or lead.ai_draft_reply or "" }}</textarea>
    
    <div class="reply-actions">
      <span class="char-count"><span id="char-count">0</span>/280</span>
      
      {% if lead.status != "sent" %}
      <button type="submit" formaction="/leads/{{ lead.id }}/regenerate" formmethod="post" class="btn btn-secondary">
        Regenerate Draft
      </button>
      <button type="submit" class="btn btn-secondary">Save Draft</button>
      <button type="submit" formaction="/leads/{{ lead.id }}/approve" formmethod="post" class="btn btn-primary" {% if sent_today >= daily_limit %}disabled{% endif %}>
        {% if sent_today >= daily_limit %}Daily Limit Reached{% else %}Approve & Send{% endif %}
      </button>
      {% endif %}
    </div>
    
    <div class="private-notes">
      <h4>Private Notes (only you see this)</h4>
      <textarea name="notes" rows="2" placeholder="Add notes about this lead...">{{ lead.notes or "" }}</textarea>
    </div>
  </form>
  
</div>

<script>
  const textarea = document.getElementById('reply-text');
  const count = document.getElementById('char-count');
  textarea.addEventListener('input', () => {
    count.textContent = textarea.value.length;
  });
  count.textContent = textarea.value.length;
</script>

{% endblock %}
```

### Step 3: Create lead_sources.html

Create `src/threads_analytics/web/templates/lead_sources.html`:

```html
{% extends "base.html" %}
{% block content %}

<div class="sources-header">
  <h1>Lead Sources</h1>
  <a href="/leads/sources/new" class="cta">+ Create Source</a>
</div>

<div class="sources-list">
  {% for source in sources %}
  <div class="source-card {% if not source.is_active %}source-card--inactive{% endif %}">
    <div class="source-header">
      <h3>{{ source.name }}</h3>
      <span class="source-status">{% if source.is_active %}🟢 Active{% else %}🔴 Inactive{% endif %}</span>
    </div>
    
    <div class="source-keywords">
      {% for keyword in source.keywords %}
      <span class="keyword-tag">{{ keyword }}</span>
      {% endfor %}
    </div>
    
    <div class="source-meta">
      {% if source.last_searched_at %}
      <span>Last searched: {{ source.last_searched_at.strftime("%Y-%m-%d %H:%M") }}</span>
      {% else %}
      <span>Never searched</span>
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>

{% endblock %}
```

### Step 4: Create lead_source_form.html

Create `src/threads_analytics/web/templates/lead_source_form.html`:

```html
{% extends "base.html" %}
{% block content %}

<a href="/leads/sources" class="back-link">← Back to Sources</a>

<h1>{% if source %}Edit{% else %}Create{% endif %} Lead Source</h1>

<form method="post" action="{% if source %}/leads/sources/{{ source.id }}{% else %}/leads/sources{% endif %}" class="source-form">
  
  <div class="form-group">
    <label for="name">Source Name</label>
    <input type="text" id="name" name="name" value="{{ source.name if source else '' }}" required>
  </div>
  
  <div class="form-group">
    <label for="keywords">Keywords (one per line)</label>
    <textarea id="keywords" name="keywords" rows="6" required>{{ '\n'.join(source.keywords) if source else '' }}</textarea>
    <p class="help-text">Enter keywords to search for. Each line is a separate keyword search.</p>
  </div>
  
  <div class="form-group">
    <label>
      <input type="checkbox" name="is_active" value="on" {% if not source or source.is_active %}checked{% endif %}>
      Active (enable searching)
    </label>
  </div>
  
  <div class="form-actions">
    <button type="submit" class="btn btn-primary">{% if source %}Update{% else %}Create{% endif %} Source</button>
    <a href="/leads/sources" class="btn btn-secondary">Cancel</a>
  </div>
  
</form>

{% endblock %}
```

### Step 5: Modify base.html (Add sidebar item)

Find the sidebar-nav section and add after the Experiments link:

```html
<a href="/leads" aria-label="Leads">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="10"/>
    <circle cx="12" cy="12" r="3"/>
    <path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>
  </svg>
  <span class="sidebar-tooltip">Leads</span>
</a>
```

### Step 6: Commit templates

```bash
git add src/threads_analytics/web/templates/
git commit -m "feat(leads): add HTML templates for Kanban, detail, and source management"
```

---

## Task 8: Tests

**Files:**
- Create: `tests/test_leads.py`

### Step 1: Create test file

```python
"""Tests for leads module."""

import pytest
from datetime import datetime, timezone

from threads_analytics.leads import (
    generate_reply_draft,
    should_skip_post,
)
from threads_analytics.models import Lead, LeadSource


def test_should_skip_own_post():
    skip, reason = should_skip_post(
        post_text="Some text",
        author_user_id="123",
        your_user_id="123",
    )
    assert skip is True
    assert reason == "own_post"


def test_should_skip_too_many_replies():
    skip, reason = should_skip_post(
        post_text="Some text",
        author_user_id="456",
        your_user_id="123",
        reply_count=15,
    )
    assert skip is True
    assert reason == "too_many_replies"


def test_should_not_skip_valid_post():
    skip, reason = should_skip_post(
        post_text="This is a valid post about manual work",
        author_user_id="456",
        your_user_id="123",
        reply_count=2,
    )
    assert skip is False


def test_should_skip_too_short():
    skip, reason = should_skip_post(
        post_text="Hi",
        author_user_id="456",
        your_user_id="123",
    )
    assert skip is True
    assert reason == "too_short"
```

### Step 2: Run tests

Run: `source .venv/bin/activate && pytest tests/test_leads.py -v`
Expected: All tests pass

### Step 3: Commit

```bash
git add tests/test_leads.py
git commit -m "test(leads): add unit tests for lead filtering logic"
```

---

## Task 9: Final Integration Test

### Step 1: Create a test lead source

```bash
source .venv/bin/activate
threads-analytics serve &
# Then visit http://localhost:8000/leads/sources/new
# Create source with keyword: "manual data entry"
```

### Step 2: Run manual search

```bash
threads-analytics search-leads --manual
```

### Step 3: Verify in UI

- Visit http://localhost:8000/leads
- Should see Kanban board
- Click a lead to view detail
- Edit draft, save, approve

### Step 4: Final commit

```bash
git add .
git commit -m "feat(leads): complete Lead Finder module with Kanban UI"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** All requirements from design doc have tasks
- [ ] **Placeholder scan:** No TBD, TODO, or vague instructions
- [ ] **Type consistency:** Model field names match throughout
- [ ] **Error handling:** All API calls have try/except
- [ ] **Safety limits:** Daily limit and cooldown implemented
- [ ] **UI flow:** Kanban → Detail → Actions all connected

---

## Execution Options

**Plan complete and saved to `docs/superpowers/plans/2026-04-12-lead-finder.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
