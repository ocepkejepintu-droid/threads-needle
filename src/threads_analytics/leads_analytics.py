"""Lead Engine v2 — Reply analytics tracking.

Tracks response rates, template performance, and conversion funnel metrics
for leads engagement.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import LeadReply, ReplyTemplate

if TYPE_CHECKING:
    from .threads_client import ThreadsClient

log = logging.getLogger(__name__)


# Conversion stage constants for clarity
STAGE_SENT = "sent"
STAGE_RESPONDED = "responded"
STAGE_DM = "converted_to_dm"
STAGE_CALL = "converted_to_call"
STAGE_CLIENT = "converted_to_client"


def update_reply_metrics(session: Session, client: "ThreadsClient") -> dict:
    """Check for responses to our sent replies.

    For each LeadReply with has_response=False and sent > 1 hour ago:
    - Fetch thread replies via API
    - Check if author replied after our reply
    - Update has_response, response_text, response_at

    Args:
        session: Database session
        client: Threads API client for fetching replies

    Returns:
        Summary dict: {"checked": N, "new_responses": N}
    """
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    # Find replies that need checking (sent > 1 hour ago, no response yet)
    pending_replies = (
        session.query(LeadReply)
        .filter(
            LeadReply.sent_at.isnot(None),
            LeadReply.sent_at < one_hour_ago,
            LeadReply.has_response.is_(False),
        )
        .all()
    )

    checked = 0
    new_responses = 0

    for lead_reply in pending_replies:
        checked += 1

        try:
            # Fetch thread replies from API
            # The lead's thread_id is available via lead_reply.lead.thread_id
            thread_id = lead_reply.lead.thread_id
            replies_data = client._get(
                f"/{thread_id}/replies",
                params={"fields": "id,text,timestamp,username"},
            )

            # Check if author replied after our reply was sent
            author_user_id = lead_reply.lead.author_user_id
            our_reply_time = lead_reply.sent_at

            for reply in replies_data.get("data", []):
                reply_timestamp_str = reply.get("timestamp")
                reply_user_id = reply.get("user_id") or reply.get("username")

                if not reply_timestamp_str:
                    continue

                try:
                    reply_time = datetime.fromisoformat(
                        reply_timestamp_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue

                # Check if this is an author reply after our reply
                if reply_user_id == author_user_id and reply_time > our_reply_time:
                    # Found a response!
                    lead_reply.has_response = True
                    lead_reply.response_text = reply.get("text", "")
                    lead_reply.response_at = reply_time

                    # Update template stats if template was used
                    if lead_reply.template_id:
                        template = session.query(ReplyTemplate).get(lead_reply.template_id)
                        if template:
                            template.times_responded += 1

                    session.commit()
                    new_responses += 1
                    log.info(
                        "Found response to lead reply %s from author %s",
                        lead_reply.id,
                        author_user_id,
                    )
                    break

        except Exception as exc:
            log.warning("Failed to check replies for lead_reply %s: %s", lead_reply.id, exc)
            continue

    return {"checked": checked, "new_responses": new_responses}


def calculate_template_stats(session: Session) -> list[dict]:
    """Calculate response rates for all templates.

    Returns list of template statistics with response rates and winner status.

    Args:
        session: Database session

    Returns:
        List of dicts with template_id, name, times_used, times_responded,
        response_rate, and is_winner
    """
    templates = session.query(ReplyTemplate).all()
    results = []

    for template in templates:
        times_used = template.times_used
        times_responded = template.times_responded

        response_rate = 0.0
        if times_used > 0:
            response_rate = round(times_responded / times_used, 4)

        results.append(
            {
                "template_id": template.id,
                "name": template.name,
                "times_used": times_used,
                "times_responded": times_responded,
                "response_rate": response_rate,
                "is_winner": template.is_winner,
            }
        )

    return results


def get_conversion_funnel(session: Session) -> dict:
    """Get conversion rates at each stage of the lead funnel.

    Calculates the progression from sent replies to clients.

    Args:
        session: Database session

    Returns:
        Dict with counts and rates for each conversion stage:
        {
            "sent": N,
            "responded": N,
            "responded_rate": N%,
            "converted_to_dm": N,
            "dm_rate": N%,
            "converted_to_call": N,
            "call_rate": N%,
            "converted_to_client": N,
            "client_rate": N%,
        }
    """
    # Base count: all sent replies
    sent_count = (
        session.query(func.count(LeadReply.id))
        .filter(LeadReply.sent_at.isnot(None))
        .scalar()
        or 0
    )

    if sent_count == 0:
        return {
            "sent": 0,
            "responded": 0,
            "responded_rate": 0.0,
            "converted_to_dm": 0,
            "dm_rate": 0.0,
            "converted_to_call": 0,
            "call_rate": 0.0,
            "converted_to_client": 0,
            "client_rate": 0.0,
        }

    # Count at each stage
    responded_count = (
        session.query(func.count(LeadReply.id))
        .filter(LeadReply.has_response.is_(True))
        .scalar()
        or 0
    )

    dm_count = (
        session.query(func.count(LeadReply.id))
        .filter(LeadReply.converted_to_dm.is_(True))
        .scalar()
        or 0
    )

    call_count = (
        session.query(func.count(LeadReply.id))
        .filter(LeadReply.converted_to_call.is_(True))
        .scalar()
        or 0
    )

    client_count = (
        session.query(func.count(LeadReply.id))
        .filter(LeadReply.converted_to_client.is_(True))
        .scalar()
        or 0
    )

    # Calculate rates (as percentages)
    responded_rate = round((responded_count / sent_count) * 100, 2)
    dm_rate = round((dm_count / sent_count) * 100, 2)
    call_rate = round((call_count / sent_count) * 100, 2)
    client_rate = round((client_count / sent_count) * 100, 2)

    return {
        "sent": sent_count,
        "responded": responded_count,
        "responded_rate": responded_rate,
        "converted_to_dm": dm_count,
        "dm_rate": dm_rate,
        "converted_to_call": call_count,
        "call_rate": call_rate,
        "converted_to_client": client_count,
        "client_rate": client_rate,
    }


def auto_promote_winners(session: Session, threshold: float = 0.3) -> int:
    """Mark templates as winners if response_rate > threshold.

    Templates must have been used at least 5 times to be considered
    for promotion (statistical significance).

    Args:
        session: Session
        threshold: Minimum response rate to mark as winner (default 0.3 = 30%)

    Returns:
        Count of templates promoted to winner status
    """
    MIN_USAGE_FOR_PROMOTION = 5

    templates = (
        session.query(ReplyTemplate)
        .filter(
            ReplyTemplate.is_active.is_(True),
            ReplyTemplate.is_winner.is_(False),
            ReplyTemplate.times_used >= MIN_USAGE_FOR_PROMOTION,
        )
        .all()
    )

    promoted = 0

    for template in templates:
        if template.times_used > 0:
            response_rate = template.times_responded / template.times_used
            if response_rate >= threshold:
                template.is_winner = True
                promoted += 1
                log.info(
                    "Promoted template %s (%s) to winner status (rate: %.2f%%)",
                    template.id,
                    template.name,
                    response_rate * 100,
                )

    if promoted > 0:
        session.commit()

    return promoted
