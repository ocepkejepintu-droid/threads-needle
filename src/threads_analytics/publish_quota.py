"""Publishing quota helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .db import session_scope
from .models import GeneratedIdea

MAX_POSTS_PER_DAY = 4


def get_posts_today(account_id: int | None = None) -> int:
    """Count posts published in last 24 hours."""
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
    """Check whether an account is within daily publish quota."""
    posts_today = get_posts_today(account_id=account_id)
    limit = soft_cap if soft_cap is not None else MAX_POSTS_PER_DAY
    remaining = limit - posts_today
    return remaining > 0, remaining
