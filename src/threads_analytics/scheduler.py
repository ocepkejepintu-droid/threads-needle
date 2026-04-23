"""Background scheduler for auto-publishing scheduled posts and replies.

Durable, idempotent, account-aware orchestrator.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import session_scope
from .leads import send_reply
from .models import GeneratedIdea, Lead
from .planner import plan_account_items
from .publish_gate import gate_publish_idea, gate_send_reply
from .comment_inbox import poll_for_comments
from .comment_reply_drafts import draft_replies_for_inbox
from .db import init_db
from .intake.runner import expire_old_items, run_intake_cycle
from .outcome_tagger import run_outcome_tagging_cycle
from .performance_feedback import run_feedback_cycle
from .publisher import publish_scheduled_idea
from .threads_client import ThreadsClient

log = logging.getLogger(__name__)

POLL_INTERVAL = 60
# Daily intake runs at 08:00 WIB = 01:00 UTC
_INTAKE_HOUR_UTC = 1
_INTAKE_MINUTE_UTC = 0


class _SchedulerState:
    thread: threading.Thread | None = None
    stop_event: threading.Event = threading.Event()
    last_intake_date: str | None = None  # YYYY-MM-DD
    last_outcome_hour: str | None = None  # YYYY-MM-DD-HH
    last_comment_poll: datetime | None = None
    last_feedback_hour: str | None = None  # YYYY-MM-DD-HH


_state = _SchedulerState()


def _claim_due_posts() -> list[int]:
    """Claim scheduled/approved due posts atomically via claim token."""
    now = datetime.now(timezone.utc)
    claimed: list[int] = []
    with session_scope() as session:
        posts = session.scalars(
            select(GeneratedIdea)
            .where(GeneratedIdea.status.in_(["approved", "scheduled"]))
            .where(GeneratedIdea.scheduled_at <= now)
            .where(
                (GeneratedIdea.claim_token.is_(None))
                | (GeneratedIdea.claimed_at <= (datetime.now(timezone.utc) - timedelta(minutes=15)))
            )
        ).all()

        for post in posts:
            # Re-check inside transaction to avoid race
            fresh = session.get(GeneratedIdea, post.id)
            if fresh is None:
                continue
            _sched = fresh.scheduled_at
            if _sched and _sched.tzinfo is None:
                _sched = _sched.replace(tzinfo=timezone.utc)
            if fresh.status in ("approved", "scheduled") and _sched and _sched <= now:
                _claimed = fresh.claimed_at
                if _claimed and _claimed.tzinfo is None:
                    _claimed = _claimed.replace(tzinfo=timezone.utc)
                if fresh.claim_token is None or (
                    _claimed and (now - _claimed).total_seconds() > 900
                ):
                    fresh.claim_token = f"sched-{uuid.uuid4().hex[:12]}"
                    fresh.claimed_at = now
                    claimed.append(fresh.id)
    return claimed


def _claim_approved_leads() -> list[int]:
    """Claim approved leads atomically via claim token."""
    now = datetime.now(timezone.utc)
    claimed: list[int] = []
    with session_scope() as session:
        leads = session.scalars(
            select(Lead)
            .where(Lead.status == "approved")
            .where((Lead.final_reply.isnot(None)) | (Lead.ai_draft_reply.isnot(None)))
            .where(
                (Lead.claim_token.is_(None))
                | (Lead.claimed_at <= (datetime.now(timezone.utc) - timedelta(minutes=15)))
            )
            .order_by(Lead.id)
        ).all()

        for lead in leads:
            fresh = session.get(Lead, lead.id)
            if fresh and fresh.status == "approved":
                _claimed = fresh.claimed_at
                if _claimed and _claimed.tzinfo is None:
                    _claimed = _claimed.replace(tzinfo=timezone.utc)
                if fresh.claim_token is None or (
                    _claimed and (now - _claimed).total_seconds() > 900
                ):
                    fresh.claim_token = f"sched-{uuid.uuid4().hex[:12]}"
                    fresh.claimed_at = now
                    claimed.append(fresh.id)
    return claimed


def _run_account_posts(account_id: int) -> None:
    """Publish due posts for a single account using planner ranking."""
    with session_scope() as session:
        claimed_ids = [
            row[0] for row in session.execute(
                select(GeneratedIdea.id)
                .where(GeneratedIdea.account_id == account_id)
                .where(GeneratedIdea.claim_token.isnot(None))
                .where(GeneratedIdea.status.in_(["approved", "scheduled"]))
            )
        ]
    if not claimed_ids:
        return

    planned = plan_account_items(account_id)
    due_posts = [
        p for p in planned
        if p.item_type == "post" and p.score > 0 and p.item_id in claimed_ids
    ]

    for item in due_posts:
        post_id = item.item_id
        try:
            gate = gate_publish_idea(post_id)
            if not gate.allowed:
                log.warning("Scheduler blocked post %s: %s", post_id, gate.reason)
                with session_scope() as session:
                    idea = session.get(GeneratedIdea, post_id)
                    if idea:
                        idea.status = "failed"
                        idea.error_message = gate.reason
                        idea.claim_token = None
                continue

            success = publish_scheduled_idea(post_id)
            if success:
                log.info("Published scheduled post %s", post_id)
            else:
                log.warning("Failed to publish post %s", post_id)
        except Exception as e:
            log.error("Error publishing post %s: %s", post_id, e)
            with session_scope() as session:
                idea = session.get(GeneratedIdea, post_id)
                if idea:
                    idea.status = "failed"
                    idea.error_message = str(e)
                    idea.claim_token = None
        time.sleep(1)


def _run_account_replies(account_id: int) -> None:
    """Send approved replies for a single account."""
    with session_scope() as session:
        claimed_ids = [
            row[0] for row in session.execute(
                select(Lead.id)
                .where(Lead.account_id == account_id)
                .where(Lead.claim_token.isnot(None))
                .where(Lead.status == "approved")
            )
        ]
    if not claimed_ids:
        return

    planned = plan_account_items(account_id)
    due_replies = [
        p for p in planned
        if p.item_type == "reply" and p.score > 0 and p.item_id in claimed_ids
    ]

    for item in due_replies:
        lead_id = item.item_id
        try:
            gate = gate_send_reply(lead_id)
            if not gate.allowed:
                log.warning("Scheduler blocked reply %s: %s", lead_id, gate.reason)
                with session_scope() as session:
                    lead = session.get(Lead, lead_id)
                    if lead:
                        lead.claim_token = None
                continue

            with session_scope() as session:
                lead = session.get(Lead, lead_id)
                if lead and lead.status == "approved":
                    success = send_reply(session, lead)
                    if success:
                        log.info("Sent reply for lead %s", lead_id)
                    else:
                        log.warning("Failed to send reply for lead %s", lead_id)
        except Exception as e:
            log.error("Error sending reply for lead %s: %s", lead_id, e)
            with session_scope() as session:
                lead = session.get(Lead, lead_id)
                if lead:
                    lead.claim_token = None
        time.sleep(2)


def _run_comment_poll() -> None:
    """Poll for new comments. Hermes writes replies, not the app LLM."""
    from .account_scope import list_accounts
    from .pipeline import _sync_posts_for_comments

    init_db()
    with session_scope() as session:
        accounts = list_accounts(session)

    for account in accounts:
        try:
            with ThreadsClient.from_account(account) as client:
                with session_scope() as session:
                    _sync_posts_for_comments(session, client, account.id, run_id=0)
                with session_scope() as session:
                    result = poll_for_comments(session, client, account.id, run_id=0)
                if result:
                    log.info("Comment poll fetched %s comments for %s", result, account.slug)
        except Exception as exc:
            log.warning("Comment poll failed for %s: %s", account.slug, exc)


def _publisher_loop() -> None:
    """Main scheduler loop."""
    log.info("Publisher scheduler started")
    stop_event = _state.stop_event

    while not stop_event.is_set():
        try:
            # Daily intake fetch at ~08:00 WIB (01:00 UTC)
            # DISABLED: Hermes is now the sole content source.
            # Old intake fetchers (HN, RSS) are skipped.
            now = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")
            if _state.last_intake_date != today_str:
                _state.last_intake_date = today_str

            # Hourly outcome tagging for posts published ~24h ago
            hour_str = now.strftime("%Y-%m-%d-%H")
            if _state.last_outcome_hour != hour_str:
                try:
                    result = run_outcome_tagging_cycle()
                    if result["tagged"] or result["errors"]:
                        log.info("Outcome tagging complete: %s", result)
                except Exception as exc:
                    log.error("Outcome tagging cycle failed: %s", exc)
                _state.last_outcome_hour = hour_str

            # Hourly prediction feedback cycle
            if _state.last_feedback_hour != hour_str:
                try:
                    run_feedback_cycle()
                    log.info("Feedback cycle complete")
                except Exception as exc:
                    log.error("Feedback cycle failed: %s", exc)
                _state.last_feedback_hour = hour_str

            # Comment poll every 15 minutes
            if _state.last_comment_poll is None or (
                now - _state.last_comment_poll
            ) >= timedelta(minutes=15):
                try:
                    _run_comment_poll()
                except Exception as exc:
                    log.error("Comment poll failed: %s", exc)
                _state.last_comment_poll = now

            # Claim due work
            _claim_due_posts()
            _claim_approved_leads()

            # Get unique accounts with claimed work
            with session_scope() as session:
                post_account_ids = {
                    row[0]
                    for row in session.execute(
                        select(GeneratedIdea.account_id)
                        .where(GeneratedIdea.claim_token.isnot(None))
                        .where(GeneratedIdea.status.in_(["approved", "scheduled"]))
                        .distinct()
                    )
                }
                reply_account_ids = {
                    row[0]
                    for row in session.execute(
                        select(Lead.account_id)
                        .where(Lead.claim_token.isnot(None))
                        .where(Lead.status == "approved")
                        .distinct()
                    )
                }
                account_ids = sorted(post_account_ids | reply_account_ids)

            for account_id in account_ids:
                if stop_event.is_set():
                    break
                _run_account_posts(account_id)
                _run_account_replies(account_id)

            stop_event.wait(POLL_INTERVAL)
        except Exception as e:
            log.error("Error in publisher loop: %s", e)
            stop_event.wait(POLL_INTERVAL)

    log.info("Publisher scheduler stopped")


def start_scheduler() -> None:
    """Start the background publisher scheduler."""
    if _state.thread is not None and _state.thread.is_alive():
        log.warning("Scheduler already running")
        return

    _state.stop_event.clear()
    _state.thread = threading.Thread(target=_publisher_loop, daemon=True)
    _state.thread.start()
    log.info("Scheduler thread started")


def stop_scheduler() -> None:
    """Stop the background publisher scheduler."""
    if _state.thread is None or not _state.thread.is_alive():
        log.warning("Scheduler not running")
        return

    log.info("Stopping scheduler...")
    _state.stop_event.set()
    _state.thread.join(timeout=5)

    if _state.thread.is_alive():
        log.warning("Scheduler thread did not stop gracefully")
    else:
        log.info("Scheduler stopped")

    _state.thread = None


def get_scheduler_status() -> dict[str, bool | int]:
    """Get current scheduler status."""
    return {
        "running": _state.thread is not None and _state.thread.is_alive(),
        "poll_interval": POLL_INTERVAL,
    }
