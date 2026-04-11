"""Lead finder business logic — reply generation, cooldown checks, and sending."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import get_settings
from .llm_client import create_llm_client
from .models import Lead, LeadReply, LeadSearchLog, LeadSource

if TYPE_CHECKING:
    from .threads_client import ThreadsClient

log = logging.getLogger(__name__)

# Constants
MAX_DAILY_REPLIES = 10
RECONTACT_COOLDOWN_DAYS = 30

SYSTEM_PROMPT = """You are a helpful expert in AI engineering and developer tools.

Your task is to write a thoughtful, helpful reply to someone asking a question
or discussing a topic related to AI engineering, LLMs, or developer tools.

GUIDELINES:
- Be genuinely helpful — provide specific insights, not generic encouragement
- Keep it conversational and authentic — avoid corporate speak
- Don't be overly promotional — focus on helping first
- If appropriate, subtly mention relevant experience or perspective
- Keep replies under 280 characters (Threads limit)
- Don't use hashtags unless they're genuinely relevant
- Avoid sounding like a sales pitch or bot

REPLY STRUCTURE:
1. Acknowledge their specific question/problem
2. Provide a concrete insight, tip, or perspective
3. Optionally ask a follow-up question to continue the conversation

Write only the reply text, nothing else."""


def should_skip_post(
    post_text: str,
    author_user_id: str,
    your_user_id: str,
    reply_count: int,
) -> tuple[bool, str | None]:
    """Determine if a post should be skipped based on business rules.

    Returns:
        Tuple of (should_skip, reason). If should_skip is True, reason explains why.
    """
    # Skip own posts
    if author_user_id == your_user_id:
        return True, "own_post"

    # Skip posts with too many replies (likely viral, low conversion)
    if reply_count > 50:
        return True, "too_many_replies"

    # Skip very short posts (likely low quality)
    if len(post_text.strip()) < 20:
        return True, "too_short"

    # Skip posts that look like promotions/ads
    promo_keywords = ["buy now", "click link", "limited time", "sale", "discount", "promo"]
    post_lower = post_text.lower()
    if any(kw in post_lower for kw in promo_keywords):
        return True, "promotional"

    return False, None


def generate_reply_draft(
    post_text: str,
    author_bio: str | None,
    matched_keyword: str,
) -> str:
    """Generate a helpful reply draft using the LLM client.

    Args:
        post_text: The text content of the post being replied to
        author_bio: The author's bio (if available)
        matched_keyword: The keyword that matched this post

    Returns:
        The generated reply text (should be under 280 chars)
    """
    client = create_llm_client()

    bio_context = f"\nAuthor bio: {author_bio}" if author_bio else ""

    user_prompt = f"""A user posted this on Threads (matched keyword: "{matched_keyword}"):

---
{post_text}
---{bio_context}

Write a helpful, authentic reply (under 280 characters)."""

    try:
        response = client.create_message(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=300,
            temperature=0.7,
        )

        reply = response.text.strip()

        # Enforce 280 character limit
        if len(reply) > 280:
            reply = reply[:277] + "..."

        return reply

    except Exception as exc:
        log.error("Failed to generate reply draft: %s", exc)
        # Return a simple fallback reply
        return f"Great question about {matched_keyword}! I'd love to hear more about what you're working on."

    finally:
        client.close()


def send_reply(
    session: Session,
    lead: Lead,
    client: "ThreadsClient",
    template_id: int | None = None,
) -> bool:
    """Send an approved reply via the Threads API.

    Args:
        session: Database session
        lead: The lead to send reply for (must be in 'approved' status)
        client: Threads API client
        template_id: Optional ID of the template used for this reply

    Returns:
        True if sent successfully, False otherwise
    """
    # Check lead status
    if lead.status != "approved":
        log.warning("Lead %s is not approved (status=%s), skipping", lead.id, lead.status)
        return False

    # Check daily reply limit
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    replies_sent_today = (
        session.query(func.count(Lead.id))
        .filter(Lead.sent_at >= today_start)
        .scalar()
        or 0
    )

    if replies_sent_today >= MAX_DAILY_REPLIES:
        log.warning(
            "Daily reply limit reached (%s/%s), skipping lead %s",
            replies_sent_today,
            MAX_DAILY_REPLIES,
            lead.id,
        )
        return False

    # Get the reply text to send
    reply_text = lead.final_reply or lead.ai_draft_reply
    if not reply_text:
        log.error("No reply text available for lead %s", lead.id)
        return False

    # Enforce 280 character limit
    if len(reply_text) > 280:
        reply_text = reply_text[:277] + "..."
        log.warning("Truncated reply for lead %s to 280 chars", lead.id)

    try:
        # Import here to avoid circular imports
        from .threads_client import ThreadsClient

        # Post the reply via Threads API
        # Note: This assumes the client has a method to post replies
        # The Threads API requires posting to the thread's replies endpoint
        result = client._post(
            f"/{lead.thread_id}/replies",
            params={"text": reply_text},
        )

        now = datetime.now(timezone.utc)

        # Update lead status
        lead.status = "sent"
        lead.sent_at = now
        session.commit()

        # Create LeadReply record for analytics tracking
        lead_reply = LeadReply(
            lead_id=lead.id,
            template_id=template_id,
            reply_text=reply_text,
            sent_at=now,
            has_response=False,
        )
        session.add(lead_reply)

        # Update template usage stats if template was used
        if template_id:
            from .models import ReplyTemplate

            template = session.query(ReplyTemplate).get(template_id)
            if template:
                template.times_used += 1

        session.commit()

        log.info(
            "Successfully sent reply for lead %s (thread %s), created LeadReply %s",
            lead.id,
            lead.thread_id,
            lead_reply.id,
        )
        return True

    except Exception as exc:
        log.error("Failed to send reply for lead %s: %s", lead.id, exc)
        return False


def create_lead_from_post(
    session: Session,
    source: LeadSource,
    post: dict,
    matched_keyword: str,
    your_user_id: str,
) -> Lead | None:
    """Create a Lead from API post data if it passes all checks.

    Args:
        session: Database session
        source: The LeadSource that generated this lead
        post: The post data from the API (contains id, text, username, etc.)
        matched_keyword: The keyword that matched
        your_user_id: The current user's user ID (to skip own posts)

    Returns:
        The created Lead, or None if skipped/duplicate
    """
    # Extract post data
    thread_id = post.get("id")
    post_text = post.get("text", "")
    author_username = post.get("username", "")
    author_user_id = post.get("user_id") or post.get("owner", {}).get("id")
    author_bio = post.get("biography") or post.get("owner", {}).get("biography")
    permalink = post.get("permalink")
    post_created_at_str = post.get("timestamp")
    reply_count = post.get("reply_count", 0)

    if not thread_id or not post_text:
        log.debug("Skipping post without thread_id or text")
        return None

    # Check skip conditions
    should_skip, reason = should_skip_post(
        post_text=post_text,
        author_user_id=author_user_id or "",
        your_user_id=your_user_id,
        reply_count=reply_count,
    )

    if should_skip:
        log.debug("Skipping post %s: %s", thread_id, reason)
        return None

    # Check 30-day cooldown
    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(days=RECONTACT_COOLDOWN_DAYS)
    existing_recent = (
        session.query(Lead)
        .filter(
            Lead.author_user_id == author_user_id,
            Lead.created_at >= cooldown_cutoff,
        )
        .first()
    )

    if existing_recent:
        log.debug(
            "Skipping post %s: author %s contacted within %d days",
            thread_id,
            author_user_id,
            RECONTACT_COOLDOWN_DAYS,
        )
        return None

    # Check duplicate today (same day)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    existing_today = (
        session.query(Lead)
        .filter(
            Lead.author_user_id == author_user_id,
            Lead.created_at >= today_start,
        )
        .first()
    )

    if existing_today:
        log.debug(
            "Skipping post %s: author %s already has a lead today",
            thread_id,
            author_user_id,
        )
        return None

    # Parse post timestamp
    post_created_at = datetime.now(timezone.utc)
    if post_created_at_str:
        try:
            # Handle ISO format with offset
            post_created_at = datetime.fromisoformat(
                post_created_at_str.replace("Z", "+00:00")
            )
        except ValueError:
            log.warning("Could not parse post timestamp: %s", post_created_at_str)

    # Check if this exact thread already exists
    existing_thread = (
        session.query(Lead).filter(Lead.thread_id == thread_id).first()
    )

    if existing_thread:
        log.debug("Skipping post %s: thread already exists as lead %s", thread_id, existing_thread.id)
        return None

    # Create the lead
    lead = Lead(
        source_id=source.id,
        thread_id=thread_id,
        author_username=author_username,
        author_user_id=author_user_id or "",
        author_bio=author_bio,
        post_text=post_text,
        post_permalink=permalink or "",
        post_created_at=post_created_at,
        matched_keyword=matched_keyword,
        status="new",
    )

    session.add(lead)
    session.commit()

    log.info(
        "Created lead %s from thread %s (keyword: %s, author: %s)",
        lead.id,
        thread_id,
        matched_keyword,
        author_username,
    )

    return lead
