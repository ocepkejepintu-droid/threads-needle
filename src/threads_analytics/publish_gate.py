"""Unified pre-publish policy gate.

Checks explicit approval, content validity, brand alignment, quota,
and account health before any publish action.
"""

from __future__ import annotations

import logging
from importlib import import_module
from dataclasses import dataclass, field
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import content_rules
from .brand_validator import validate_content as brand_validate_content
from .db import session_scope
from .models import Account, CommentInbox, GeneratedIdea, Lead, YouProfile
from .threads_client import ThreadsClient

log = logging.getLogger(__name__)


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    violations: list[str] = field(default_factory=list)


class _PublisherModule(Protocol):
    def can_publish(
        self, account_id: int | None = None, soft_cap: int | None = None
    ) -> tuple[bool, int]: ...


def _latest_you_profile(session: Session, account_id: int) -> YouProfile | None:
    return session.scalar(
        select(YouProfile)
        .where(YouProfile.account_id == account_id)
        .order_by(YouProfile.run_id.desc())
        .limit(1)
    )


def _content_policy_checks(
    session: Session, idea: GeneratedIdea, account: Account
) -> GateResult | None:
    slop = content_rules.validate_content(idea.concept or "")
    if not slop.passed:
        return GateResult(
            allowed=False,
            reason="Anti-slop check failed",
            violations=slop.failures,
        )
    you = _latest_you_profile(session, account.id)
    if you is not None:
        brand = brand_validate_content(idea.concept or "", you)
        if not brand.passed:
            return GateResult(
                allowed=False,
                reason="Brand check failed",
                violations=[s.issue for s in brand.suggestions],
            )
    else:
        log.warning("No YouProfile for account %s; skipping brand check", account.id)
    return None


def _rubric_gate_checks(idea: GeneratedIdea) -> list[str]:
    """P1.2 rubric gate: advisory only — returns warnings, never blocks."""
    warnings: list[str] = []
    scores = [
        idea.rubric_hook_test,
        idea.rubric_mechanic_fit,
        idea.rubric_operator_standing,
        idea.rubric_trend_freshness,
        idea.rubric_reply_invitation,
        idea.rubric_voice_signature,
    ]
    if any(s is None for s in scores):
        missing = []
        names = ["hook_test", "mechanic_fit", "operator_standing",
                 "trend_freshness", "reply_invitation", "voice_signature"]
        for name, score in zip(names, scores):
            if score is None:
                missing.append(name)
        warnings.append(f"Rubric incomplete: missing {', '.join(missing)}")

    if not idea.mechanic:
        warnings.append("Mechanic tag missing")

    if len(idea.concept or "") < 40:
        warnings.append("Body text < 40 characters")

    return warnings


def gate_approve_idea(idea_id: int) -> GateResult:
    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        if idea is None:
            return GateResult(allowed=False, reason="Idea not found")

        account = session.get(Account, idea.account_id)
        if account is None:
            return GateResult(allowed=False, reason="Account not found")

        if idea.status == "published" or idea.thread_id is not None:
            return GateResult(allowed=False, reason="Already published")

        if "publish" not in (account.enabled_capabilities or []):
            return GateResult(allowed=False, reason="Publishing not enabled for this account")

        # Rubric checks are advisory only — log warnings but don't block
        rubric_warnings = _rubric_gate_checks(idea)
        for warning in rubric_warnings:
            log.warning("Rubric advisory for idea %s: %s", idea_id, warning)

        # Content policy checks are advisory only — log warnings but don't block
        policy = _content_policy_checks(session, idea, account)
        if policy is not None:
            log.warning("Content policy advisory for idea %s: %s", idea_id, policy.reason)

        return GateResult(allowed=True)


def gate_publish_idea(idea_id: int) -> GateResult:
    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        if idea is None:
            return GateResult(allowed=False, reason="Idea not found")

        account = session.get(Account, idea.account_id)
        if account is None:
            return GateResult(allowed=False, reason="Account not found")

        if idea.status == "published" or idea.thread_id is not None:
            return GateResult(allowed=False, reason="Already published")

        if idea.status not in ("approved", "scheduled"):
            return GateResult(allowed=False, reason="Approval required")

        if "publish" not in (account.enabled_capabilities or []):
            return GateResult(allowed=False, reason="Publishing not enabled for this account")

        publisher = cast(
            _PublisherModule, cast(object, import_module(".publisher", package=__package__))
        )
        post_soft_cap = cast(int | None, (account.soft_caps or {}).get("posts_per_day"))
        can_pub, _remaining = publisher.can_publish(account_id=account.id, soft_cap=post_soft_cap)
        if not can_pub:
            return GateResult(allowed=False, reason="Quota exceeded")

        # Content policy checks are advisory only — log warnings but don't block
        policy = _content_policy_checks(session, idea, account)
        if policy is not None:
            log.warning("Content policy advisory for idea %s: %s", idea_id, policy.reason)

        # Duplicate publish check
        if idea.thread_id is not None:
            return GateResult(allowed=False, reason="Already published")

        # Token validity check
        try:
            _ = ThreadsClient.from_account(account)
        except Exception as exc:
            log.warning("Token validation failed for account %s: %s", account.id, exc)
            return GateResult(allowed=False, reason="Invalid account token")

        return GateResult(allowed=True)


def gate_send_reply(lead_id: int) -> GateResult:
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            return GateResult(allowed=False, reason="Lead not found")

        if lead.status == "sent":
            return GateResult(allowed=False, reason="Already sent")

        if lead.status != "approved":
            return GateResult(allowed=False, reason="Approval required")

        account = session.get(Account, lead.account_id)
        if account is None:
            return GateResult(allowed=False, reason="Account not found")

        from datetime import datetime, timezone
        from sqlalchemy import func

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        replies_sent_today = (
            session.query(func.count(Lead.id))
            .filter(Lead.account_id == account.id)
            .filter(Lead.sent_at >= today_start)
            .scalar()
            or 0
        )

        from .leads import MAX_DAILY_REPLIES

        soft_cap = cast(int, (account.soft_caps or {}).get("replies_per_day", MAX_DAILY_REPLIES))
        if replies_sent_today >= soft_cap:
            return GateResult(allowed=False, reason="Daily reply quota exceeded")

        reply_text = lead.final_reply or lead.ai_draft_reply or ""

        # Anti-slop and brand checks are advisory only for replies
        slop = content_rules.validate_content(reply_text)
        if not slop.passed:
            log.warning("Reply anti-slop advisory for lead %s: %s", lead.id, slop.failures)

        you = _latest_you_profile(session, account.id)
        if you is not None:
            brand = brand_validate_content(reply_text, you)
            if not brand.passed:
                log.warning("Reply brand advisory for lead %s: %s", lead.id, brand.suggestions)
        else:
            log.warning("No YouProfile for account %s; skipping brand check", account.id)

        if "reply" not in (account.enabled_capabilities or []):
            return GateResult(allowed=False, reason="Replying not enabled for this account")

        try:
            _ = ThreadsClient.from_account(account)
        except Exception as exc:
            log.warning("Token validation failed for account %s: %s", account.id, exc)
            return GateResult(allowed=False, reason="Invalid account token")

        return GateResult(allowed=True)


def gate_send_comment(inbox_id: int) -> GateResult:
    with session_scope() as session:
        inbox_item = session.get(CommentInbox, inbox_id)
        if inbox_item is None:
            return GateResult(allowed=False, reason="Comment inbox item not found")

        if inbox_item.status == CommentInbox.STATUS_SENT:
            return GateResult(allowed=False, reason="Already sent")

        if inbox_item.status != CommentInbox.STATUS_APPROVED:
            return GateResult(allowed=False, reason="Approval required")

        account = session.get(Account, inbox_item.account_id)
        if account is None:
            return GateResult(allowed=False, reason="Account not found")

        from datetime import datetime, timezone
        from sqlalchemy import func

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        comment_replies_sent_today = (
            session.query(func.count(CommentInbox.id))
            .filter(CommentInbox.account_id == account.id)
            .filter(CommentInbox.sent_at >= today_start)
            .scalar()
            or 0
        )
        lead_replies_sent_today = (
            session.query(func.count(Lead.id))
            .filter(Lead.account_id == account.id)
            .filter(Lead.sent_at >= today_start)
            .scalar()
            or 0
        )
        replies_sent_today = comment_replies_sent_today + lead_replies_sent_today

        from .leads import MAX_DAILY_REPLIES

        soft_cap = cast(int, (account.soft_caps or {}).get("replies_per_day", MAX_DAILY_REPLIES))
        if replies_sent_today >= soft_cap:
            return GateResult(allowed=False, reason="Daily reply quota exceeded")

        reply_text = inbox_item.final_reply or inbox_item.ai_draft_reply or ""

        # Anti-slop and brand checks are advisory only for replies
        slop = content_rules.validate_content(reply_text)
        if not slop.passed:
            log.warning("Comment anti-slop advisory for inbox %s: %s", inbox_item.id, slop.failures)

        you = _latest_you_profile(session, account.id)
        if you is not None:
            brand = brand_validate_content(reply_text, you)
            if not brand.passed:
                log.warning("Comment brand advisory for inbox %s: %s", inbox_item.id, brand.suggestions)
        else:
            log.warning("No YouProfile for account %s; skipping brand check", account.id)

        if "reply" not in (account.enabled_capabilities or []):
            return GateResult(allowed=False, reason="Replying not enabled for this account")

        try:
            _ = ThreadsClient.from_account(account)
        except Exception as exc:
            log.warning("Token validation failed for account %s: %s", account.id, exc)
            return GateResult(allowed=False, reason="Invalid account token")

        return GateResult(allowed=True)


def invalidate_approval_on_edit(idea_id: int) -> bool:
    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        if idea is None:
            return False
        if idea.status in ("approved", "scheduled"):
            idea.status = "draft"
            idea.scheduled_at = None
            idea.claim_token = None
            return True
        return False
