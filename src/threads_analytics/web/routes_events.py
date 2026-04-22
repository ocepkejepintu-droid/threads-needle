"""Webhook and notification routes."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..account_scope import get_account_by_slug, get_or_create_default_account
from ..db import session_scope
from ..models import Account, Notification
from ..notifier import create_notification, dismiss_notification


def _extract_payload_user_ids(payload: object) -> set[str]:
    user_ids: set[str] = set()

    def _walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"user_id", "threads_user_id", "id", "from", "recipient"}:
                    if isinstance(item, dict):
                        nested_id = item.get("id") or item.get("user_id")
                        if nested_id is not None:
                            user_ids.add(str(nested_id))
                    elif item is not None:
                        user_ids.add(str(item))
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    entry = payload.get("entry") if isinstance(payload, dict) else None
    if entry is not None:
        _walk(entry)
    _walk(payload)
    return {user_id for user_id in user_ids if user_id and user_id != "unknown"}


def register_events_routes(router) -> None:
    def _resolve_account(session, account: str | None):
        acct = (
            get_account_by_slug(session, account)
            if account
            else get_or_create_default_account(session)
        )
        return acct if acct is not None else get_or_create_default_account(session)

    @router.post("/webhook/threads")
    async def threads_webhook(request: Request) -> JSONResponse:
        """Receive Threads webhook events — accelerates but does not replace polling."""
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        # Default to default account if no explicit account mapping
        with session_scope() as session:
            candidate_ids = _extract_payload_user_ids(payload)
            accounts: list[Account] = []

            if candidate_ids:
                accounts = list(
                    session.scalars(
                        select(Account).where(Account.threads_user_id.in_(candidate_ids))
                    ).all()
                )

            if not accounts:
                return JSONResponse(
                    {"error": "Unable to map webhook to an account"}, status_code=400
                )

        event_type = payload.get("object", "unknown")
        entry = (payload.get("entry") or [{}])[0]
        changes = entry.get("changes", [])

        if changes:
            for account in accounts:
                link_path = (
                    f"/accounts/{account.slug}/content" if account.slug != "default" else "/content"
                )
                create_notification(
                    account_id=account.id,
                    alert_type="webhook_event",
                    title="Threads event received",
                    message=f"Event type: {event_type}",
                    link_path=link_path,
                )

        return JSONResponse({"success": True})

    @router.get("/api/notifications")
    def api_notifications(account: str | None = None) -> JSONResponse:
        if account:
            from fastapi.responses import RedirectResponse

            return RedirectResponse(f"/accounts/{account}/api/notifications", status_code=303)
        return JSONResponse({"error": "Account required"}, status_code=400)

    @router.get("/accounts/{account_slug}/api/notifications")
    def api_notifications_prefixed(account_slug: str) -> JSONResponse:
        with session_scope() as session:
            acct = get_account_by_slug(session, account_slug)
            if acct is None:
                return JSONResponse({"error": "Account not found"}, status_code=404)
            notes = list(
                session.scalars(
                    select(Notification)
                    .where(Notification.account_id == acct.id)
                    .where(Notification.is_dismissed.is_(False))
                    .order_by(Notification.created_at.desc())
                    .limit(20)
                ).all()
            )
        return JSONResponse(
            {
                "notifications": [
                    {
                        "id": n.id,
                        "alert_type": n.alert_type,
                        "title": n.title,
                        "message": n.message,
                        "link_path": n.link_path,
                        "created_at": n.created_at.isoformat() if n.created_at else None,
                    }
                    for n in notes
                ]
            }
        )

    @router.post("/api/notifications/{note_id}/dismiss")
    def api_dismiss_notification(note_id: int, account: str | None = None) -> JSONResponse:
        if account:
            from fastapi.responses import RedirectResponse

            return RedirectResponse(
                f"/accounts/{account}/api/notifications/{note_id}/dismiss",
                status_code=303,
            )
        return JSONResponse({"error": "Account required"}, status_code=400)

    @router.post("/accounts/{account_slug}/api/notifications/{note_id}/dismiss")
    def api_dismiss_notification_prefixed(account_slug: str, note_id: int) -> JSONResponse:
        with session_scope() as session:
            acct = get_account_by_slug(session, account_slug)
            if acct is None:
                return JSONResponse({"error": "Account not found"}, status_code=404)
        ok = dismiss_notification(note_id, account_id=acct.id)
        return JSONResponse({"success": ok})
