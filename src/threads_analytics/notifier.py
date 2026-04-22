"""In-app notification generator.

Creates account-scoped alerts for workflow events.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .db import session_scope
from .models import Notification


def create_notification(
    account_id: int,
    alert_type: str,
    title: str,
    message: str,
    link_path: str | None = None,
) -> Notification:
    with session_scope() as session:
        note = Notification(
            account_id=account_id,
            alert_type=alert_type,
            title=title,
            message=message,
            link_path=link_path,
        )
        session.add(note)
        session.flush()
        return note


def get_active_notifications(account_id: int, limit: int = 20) -> list[Notification]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(Notification)
                .where(Notification.account_id == account_id)
                .where(Notification.is_dismissed.is_(False))
                .order_by(Notification.created_at.desc())
                .limit(limit)
            ).all()
        )


def dismiss_notification(note_id: int, account_id: int | None = None) -> bool:
    with session_scope() as session:
        note = session.get(Notification, note_id)
        if note is None:
            return False
        if account_id is not None and note.account_id != account_id:
            return False
        note.is_dismissed = True
        note.dismissed_at = datetime.now(timezone.utc)
        return True
