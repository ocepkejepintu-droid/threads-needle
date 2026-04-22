"""Threads API publishing service.

Handles posting to Threads with rate limit tracking.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .account_scope import get_or_create_default_account
from .db import session_scope
from .models import Account, GeneratedIdea, PublishLedger
from .threads_client import ThreadsClient

log = logging.getLogger(__name__)

# Rate limits
MAX_POSTS_PER_DAY = 4


def _resolve_account_id(account: Account | None) -> int:
    if account is not None and account.id is not None:
        return account.id

    with session_scope() as session:
        return get_or_create_default_account(session).id


def _get_account_by_id(account_id: int) -> Account | None:
    with session_scope() as session:
        return session.get(Account, account_id)


def gate_publish_idea(idea_id: int):
    from .publish_gate import gate_publish_idea as _gate_publish_idea

    return _gate_publish_idea(idea_id)


def ensure_pending_publish_ledger(
    session,
    *,
    account_id: int,
    source_type: str,
    source_id: int | None,
    workflow_type: str,
    approval_timestamp: datetime | None = None,
) -> PublishLedger:
    stmt = (
        select(PublishLedger)
        .where(PublishLedger.account_id == account_id)
        .where(PublishLedger.source_type == source_type)
        .where(PublishLedger.workflow_type == workflow_type)
        .order_by(PublishLedger.created_at.desc(), PublishLedger.id.desc())
        .limit(1)
    )
    if source_id is None:
        stmt = stmt.where(PublishLedger.source_id.is_(None))
    else:
        stmt = stmt.where(PublishLedger.source_id == source_id)

    existing = session.scalar(stmt)
    if (
        existing is not None
        and existing.status == "pending"
        and existing.thread_id is None
        and existing.creation_id is None
    ):
        if approval_timestamp is not None:
            existing.approval_timestamp = approval_timestamp
        existing.error_code = None
        existing.recovery_source = None
        existing.updated_at = datetime.now(timezone.utc)
        return existing

    ledger = PublishLedger(
        account_id=account_id,
        source_type=source_type,
        source_id=source_id,
        workflow_type=workflow_type,
        approval_timestamp=approval_timestamp,
        status="pending",
    )
    session.add(ledger)
    session.flush()
    return ledger


def _start_publish_attempt(
    *,
    account_id: int,
    source_type: str,
    source_id: int | None,
    workflow_type: str,
    approval_timestamp: datetime | None = None,
) -> int:
    with session_scope() as session:
        ledger = ensure_pending_publish_ledger(
            session,
            account_id=account_id,
            source_type=source_type,
            source_id=source_id,
            workflow_type=workflow_type,
            approval_timestamp=approval_timestamp,
        )
        session.flush()
        return ledger.id


def _finalize_publish_attempt(
    ledger_id: int,
    *,
    status: str,
    creation_id: str | None = None,
    thread_id: str | None = None,
    error_code: str | None = None,
    recovery_source: str | None = None,
) -> None:
    with session_scope() as session:
        ledger = session.get(PublishLedger, ledger_id)
        if ledger is None:
            return

        ledger.status = status
        if creation_id is not None:
            ledger.creation_id = creation_id
        if thread_id is not None:
            ledger.thread_id = thread_id
        ledger.error_code = error_code
        ledger.recovery_source = recovery_source
        ledger.updated_at = datetime.now(timezone.utc)


def _extract_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return f"http_{status_code}"
    return exc.__class__.__name__


def get_posts_today(account_id: int | None = None) -> int:
    """Count posts published in last 24 hours."""
    from sqlalchemy import func, select

    day_ago = datetime.now(timezone.utc) - timedelta(days=1)

    with session_scope() as session:
        stmt = (
            select(func.count(GeneratedIdea.id))
            .where(GeneratedIdea.status == "published")
            .where(GeneratedIdea.posted_at >= day_ago)
        )
        if account_id is not None:
            stmt = stmt.where(GeneratedIdea.account_id == account_id)
        count = session.scalar(stmt)
        return count or 0


def can_publish(account_id: int | None = None, soft_cap: int | None = None) -> tuple[bool, int]:
    """Check if we can publish (within rate limits)."""
    posts_today = get_posts_today(account_id=account_id)
    limit = soft_cap if soft_cap is not None else MAX_POSTS_PER_DAY
    remaining = limit - posts_today
    return remaining > 0, remaining


def _is_local_url(url: str) -> bool:
    """Check if a URL points to localhost or a private network address."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    if hostname.startswith(
        (
            "10.",
            "192.168.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
        )
    ):
        return True
    return False


def publish_post(
    text: str,
    image_url: str | None = None,
    account: Account | None = None,
    account_id: int | None = None,
    *,
    source_type: str = "manual",
    source_id: int | None = None,
    workflow_type: str = "post",
    approval_timestamp: datetime | None = None,
    soft_cap: int | None = None,
) -> str:
    """Publish a post to Threads.

    Args:
        text: Post content (max 280 chars)
        image_url: Optional image URL to attach

    Returns:
        thread_id: The published post ID

    Raises:
        RuntimeError: If publishing fails or rate limited
    """
    # Check rate limits
    resolved_account: Account | None = account
    if resolved_account is None and account_id is not None:
        resolved_account = _get_account_by_id(account_id)

    resolved_soft_cap = soft_cap
    if resolved_soft_cap is None and resolved_account is not None:
        resolved_soft_cap = (resolved_account.soft_caps or {}).get("posts_per_day")
    can_pub, remaining = can_publish(
        account_id=resolved_account.id if resolved_account else account_id,
        soft_cap=resolved_soft_cap,
    )
    if not can_pub:
        limit = resolved_soft_cap if resolved_soft_cap is not None else MAX_POSTS_PER_DAY
        raise RuntimeError(f"Rate limit reached: {limit} posts per day")

    # Validate content
    if not text or len(text.strip()) < 1:
        raise RuntimeError("Post text is empty")

    # Truncate if needed (Threads limit is 280 chars)
    if len(text) > 280:
        text = text[:277] + "..."

    # Validate image URL is publicly accessible
    if image_url:
        if image_url.startswith("/") or _is_local_url(image_url):
            raise RuntimeError(
                "This image is stored locally and cannot be published to Threads. "
                "Meta's servers need a publicly accessible image URL. "
                "Please use a public image URL (e.g., from imgur, Cloudinary, etc.) instead."
            )

    account_id = _resolve_account_id(resolved_account)
    ledger_id = _start_publish_attempt(
        account_id=account_id,
        source_type=source_type,
        source_id=source_id,
        workflow_type=workflow_type,
        approval_timestamp=approval_timestamp,
    )

    creation_id: str | None = None
    try:
        client = ThreadsClient.from_account(resolved_account)

        # Publish text or image post
        result = client.create_text_post(text, image_url=image_url)

        creation_id = result.get("creation_id")
        thread_id = result.get("id")
        if not thread_id:
            raise RuntimeError(f"No thread ID in response: {result}")

        _finalize_publish_attempt(
            ledger_id,
            status="published",
            creation_id=creation_id,
            thread_id=thread_id,
        )
        log.info("Published post: %s", thread_id)
        return thread_id

    except Exception as e:
        _finalize_publish_attempt(
            ledger_id,
            status="failed",
            creation_id=creation_id,
            error_code=_extract_error_code(e),
        )
        log.error("Failed to publish post: %s", e)
        raise RuntimeError(f"Publishing failed: {e}") from e


def publish_scheduled_idea(idea_id: int) -> bool:
    """Publish a scheduled idea by ID.

    Args:
        idea_id: The GeneratedIdea ID

    Returns:
        True if published successfully
    """
    gate = gate_publish_idea(idea_id)
    if not gate.allowed:
        log.warning("Publish gate blocked idea %s: %s", idea_id, gate.reason)
        with session_scope() as session:
            idea = session.get(GeneratedIdea, idea_id)
            if idea:
                idea.status = "failed"
                idea.error_message = gate.reason
        return False

    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        if not idea:
            log.error("Idea %s not found", idea_id)
            return False

        if idea.status not in ("approved", "scheduled"):
            log.warning("Idea %s is not approved or scheduled (status: %s)", idea_id, idea.status)
            return False

        account = session.get(Account, idea.account_id)
        if account is None and idea.account_id == 1:
            account = get_or_create_default_account(session)

        concept = idea.concept
        image_url = idea.image_url
        approval_timestamp = idea.scheduled_at or datetime.now(timezone.utc)

    try:
        thread_id = publish_post(
            concept,
            image_url=image_url,
            account=account,
            source_type="idea",
            source_id=idea_id,
            workflow_type="post",
            approval_timestamp=approval_timestamp,
        )

        with session_scope() as session:
            idea = session.get(GeneratedIdea, idea_id)
            if not idea:
                log.error("Idea %s disappeared before publish completion", idea_id)
                return False

            idea.status = "published"
            idea.thread_id = thread_id
            idea.actual_post_id = thread_id
            idea.posted_at = datetime.now(timezone.utc)
            idea.error_message = None

            # Auto-track in experiment if linked
            if idea.experiment_id:
                _track_in_experiment(session, idea, thread_id)

            log.info("Published scheduled idea %s as thread %s", idea_id, thread_id)
            return True

    except Exception as e:
        with session_scope() as session:
            idea = session.get(GeneratedIdea, idea_id)
            if idea is None:
                log.error("Idea %s disappeared after publish failure: %s", idea_id, e)
                return False
            idea.status = "failed"
            idea.error_message = str(e)
        log.error("Failed to publish idea %s: %s", idea_id, e)
        return False


def _track_in_experiment(session, idea, thread_id: str) -> None:
    """Track a published post in its linked experiment."""
    from .models import ExperimentPostClassification, Experiment

    experiment = session.get(Experiment, idea.experiment_id)
    if not experiment:
        log.warning("Experiment %s not found for idea %s", idea.experiment_id, idea.id)
        return

    # Only track if experiment is active
    if experiment.status not in ("active", "completed"):
        log.info("Experiment %s is not active, skipping tracking", experiment.id)
        return

    account = session.get(Account, idea.account_id)
    account_id = account.id if account else 1
    bucket = experiment.category.lower() if experiment.category else "variant"
    if bucket not in ("variant", "control"):
        bucket = "variant"

    classification = ExperimentPostClassification(
        account_id=account_id,
        experiment_id=experiment.id,
        post_thread_id=thread_id,
        bucket=bucket,
        reason="Published via scheduler",
    )
    session.add(classification)
    log.info("Tracked post %s in experiment %s", thread_id, experiment.id)
