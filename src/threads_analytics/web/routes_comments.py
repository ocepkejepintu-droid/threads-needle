"""Mission Control comment inbox routes."""

from __future__ import annotations

from typing import Mapping

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..comment_inbox import (
    bulk_approve_comments,
    bulk_ignore_comments,
    bulk_unapprove_comments,
    edit_comment_reply,
    send_selected_comments,
)
from ..comment_reply_drafts import draft_replies_for_inbox
from ..db import session_scope
from ..models import CommentInbox
from .routes_common import (
    redirect_to_account_route,
    reject_ambiguous_account_mutation,
    require_account,
    with_account_context,
)


def _parse_ids(payload: Mapping[str, object]) -> list[int]:
    raw_ids = payload.get("ids")
    if not isinstance(raw_ids, list):
        return []
    ids: list[int] = []
    for value in raw_ids:
        if isinstance(value, bool):
            continue
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _scoped_inbox_items(session: Session, account_id: int, ids: list[int]) -> list[CommentInbox]:
    if not ids:
        return []
    return list(
        session.scalars(
            select(CommentInbox)
            .where(CommentInbox.account_id == account_id, CommentInbox.id.in_(ids))
            .order_by(CommentInbox.id)
        )
    )


def _load_scoped_item(session: Session, account_id: int, inbox_id: int) -> CommentInbox | None:
    return session.scalar(
        select(CommentInbox).where(
            CommentInbox.account_id == account_id, CommentInbox.id == inbox_id
        )
    )


def _validate_scoped_ids(
    session: Session, account_id: int, ids: list[int]
) -> list[CommentInbox] | None:
    items = _scoped_inbox_items(session, account_id, ids)
    if len(items) < len(set(ids)):
        return None
    return items


def _render_comments_template(
    request: Request, templates: Jinja2Templates, context: Mapping[str, object]
) -> HTMLResponse:
    template_context = dict(context)
    try:
        return templates.TemplateResponse(
            request, "comments_mission_control.html", template_context
        )
    except TemplateNotFound:
        rows: list[str] = []
        inbox_items = template_context.get("inbox_items", [])
        for item in inbox_items if isinstance(inbox_items, list) else []:
            if not isinstance(item, dict):
                continue
            rows.append(
                (
                    "<article>"
                    f"<h2>{item['comment_author_username']}</h2>"
                    f"<p>{item['comment_text']}</p>"
                    f"<p>{item['source_post_text']}</p>"
                    f"<p>{item['status']}</p>"
                    "</article>"
                )
            )
        return HTMLResponse("".join(rows) or "<p>No inbox items</p>")


def _draft_replies(session: Session, account_id: int, ids: list[int]) -> int:
    return draft_replies_for_inbox(session, account_id, inbox_ids=ids, force_regenerate=True)


def register_comments_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    @router.get("/comments", response_class=HTMLResponse)
    def comments_legacy(request: Request) -> Response:
        return redirect_to_account_route(
            "/comments", account_slug=request.query_params.get("account")
        )

    @router.get("/accounts/{account_slug}/comments", response_class=HTMLResponse)
    def comments_prefixed(
        request: Request,
        account_slug: str,
        archived: bool = Query(False, description="Include sent (already-replied) comments"),
    ) -> HTMLResponse:
        with session_scope() as session:
            account = require_account(session, account_slug)
            stmt = (
                select(CommentInbox)
                .where(CommentInbox.account_id == account.id)
                .order_by(desc(CommentInbox.comment_created_at))
            )
            if not archived:
                stmt = stmt.where(CommentInbox.status != CommentInbox.STATUS_SENT)

            items = session.scalars(stmt).all()

            inbox_items = [
                {
                    "id": item.id,
                    "source_post_thread_id": item.source_post_thread_id,
                    "source_post_text": item.source_post_text,
                    "comment_thread_id": item.comment_thread_id,
                    "comment_text": item.comment_text,
                    "comment_author_username": item.comment_author_username,
                    "status": item.status,
                    "ai_draft_reply": item.ai_draft_reply,
                    "final_reply": item.final_reply,
                    "send_error": item.send_error,
                    "approved_at": item.approved_at,
                    "sent_at": item.sent_at,
                }
                for item in items
            ]
            counts = {
                "drafted": sum(1 for item in items if item.status == CommentInbox.STATUS_DRAFTED),
                "approved": sum(1 for item in items if item.status == CommentInbox.STATUS_APPROVED),
                "sending": sum(1 for item in items if item.status == CommentInbox.STATUS_SENDING),
                "sent": sum(1 for item in items if item.status == CommentInbox.STATUS_SENT),
                "send_failed": sum(
                    1 for item in items if item.status == CommentInbox.STATUS_SEND_FAILED
                ),
                "ignored": sum(1 for item in items if item.status == CommentInbox.STATUS_IGNORED),
                "total": len(items),
            }

        return _render_comments_template(
            request,
            templates,
            with_account_context(
                account_slug,
                inbox_items=inbox_items,
                counts=counts,
                archived=archived,
            ),
        )

    @router.post("/comments/api/approve")
    def comments_approve_legacy() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/comments/api/unapprove")
    def comments_unapprove_legacy() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/comments/api/ignore")
    def comments_ignore_legacy() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/comments/api/send")
    def comments_send_legacy() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/comments/api/edit")
    def comments_edit_legacy() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/comments/api/regenerate")
    def comments_regenerate_legacy() -> JSONResponse:
        return reject_ambiguous_account_mutation()

    @router.post("/accounts/{account_slug}/comments/api/approve")
    async def comments_approve(request: Request, account_slug: str) -> JSONResponse:
        payload = await request.json()
        ids = _parse_ids(payload)
        with session_scope() as session:
            account = require_account(session, account_slug)
            if _validate_scoped_ids(session, account.id, ids) is None:
                return JSONResponse({"error": "One or more items not found"}, status_code=404)
            approved = bulk_approve_comments(session, ids)
        return JSONResponse({"success": True, "approved": approved})

    @router.post("/accounts/{account_slug}/comments/api/unapprove")
    async def comments_unapprove(request: Request, account_slug: str) -> JSONResponse:
        payload = await request.json()
        ids = _parse_ids(payload)
        with session_scope() as session:
            account = require_account(session, account_slug)
            if _validate_scoped_ids(session, account.id, ids) is None:
                return JSONResponse({"error": "One or more items not found"}, status_code=404)
            unapproved = bulk_unapprove_comments(session, ids)
        return JSONResponse({"success": True, "unapproved": unapproved})

    @router.post("/accounts/{account_slug}/comments/api/ignore")
    async def comments_ignore(request: Request, account_slug: str) -> JSONResponse:
        payload = await request.json()
        ids = _parse_ids(payload)
        with session_scope() as session:
            account = require_account(session, account_slug)
            if _validate_scoped_ids(session, account.id, ids) is None:
                return JSONResponse({"error": "One or more items not found"}, status_code=404)
            ignored = bulk_ignore_comments(session, ids)
        return JSONResponse({"success": True, "ignored": ignored})

    @router.post("/accounts/{account_slug}/comments/api/send")
    async def comments_send(request: Request, account_slug: str) -> JSONResponse:
        payload = await request.json()
        ids = _parse_ids(payload)
        with session_scope() as session:
            account = require_account(session, account_slug)
            items = _validate_scoped_ids(session, account.id, ids)
            if items is None:
                return JSONResponse({"error": "One or more items not found"}, status_code=404)
            summary = send_selected_comments(session, [item.id for item in items])
        return JSONResponse({"success": True, **summary})

    @router.post("/accounts/{account_slug}/comments/api/edit")
    async def comments_edit(request: Request, account_slug: str) -> JSONResponse:
        payload = await request.json()
        inbox_id = payload.get("id")
        text = payload.get("text", "")
        try:
            inbox_id = int(inbox_id)
        except (TypeError, ValueError):
            return JSONResponse({"error": "Not found"}, status_code=404)

        with session_scope() as session:
            account = require_account(session, account_slug)
            if _load_scoped_item(session, account.id, inbox_id) is None:
                return JSONResponse({"error": "Not found"}, status_code=404)
            _ = edit_comment_reply(session, inbox_id, str(text))
        return JSONResponse({"success": True})

    @router.post("/accounts/{account_slug}/comments/api/regenerate")
    async def comments_regenerate(request: Request, account_slug: str) -> JSONResponse:
        payload = await request.json()
        ids = _parse_ids(payload)
        with session_scope() as session:
            account = require_account(session, account_slug)
            items = _validate_scoped_ids(session, account.id, ids)
            if items is None:
                return JSONResponse({"error": "One or more items not found"}, status_code=404)
            drafted = _draft_replies(session, account.id, [item.id for item in items])
        return JSONResponse({"success": True, "drafted": drafted})

    # ── Hermes comment bridge ─────────────────────────────────────────────
    @router.get("/accounts/{account_slug}/comments/api/pending")
    def comments_pending_json(request: Request, account_slug: str) -> JSONResponse:
        """Return pending comments that need replies (for Hermes / external agents)."""
        with session_scope() as session:
            account = require_account(session, account_slug)
            items = session.scalars(
                select(CommentInbox)
                .where(
                    CommentInbox.account_id == account.id,
                    CommentInbox.status.in_([
                        CommentInbox.STATUS_DRAFTED,
                        CommentInbox.STATUS_APPROVED,
                    ]),
                )
                .order_by(desc(CommentInbox.comment_created_at))
                .limit(50)
            ).all()
            payload = [
                {
                    "id": item.id,
                    "comment_author_username": item.comment_author_username,
                    "comment_text": item.comment_text,
                    "source_post_text": item.source_post_text[:200] if item.source_post_text else "",
                    "ai_draft_reply": item.ai_draft_reply,
                    "final_reply": item.final_reply,
                    "status": item.status,
                    "comment_permalink": item.comment_permalink,
                    "comment_created_at": item.comment_created_at.isoformat() if item.comment_created_at else None,
                    "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
                }
                for item in items
            ]
        return JSONResponse({"success": True, "comments": payload, "count": len(payload)})

    @router.post("/accounts/{account_slug}/api/hermes/comments/reply")
    async def hermes_comment_reply(request: Request, account_slug: str) -> JSONResponse:
        """Hermes replies to a specific comment. Auto-approves and sends if requested."""
        from ..config import get_settings
        from ..publish_gate import gate_send_comment

        settings = get_settings()
        expected_key = settings.hermes_api_key or ""
        if expected_key:
            provided_key = request.headers.get("X-Hermes-Key", "")
            if not provided_key or provided_key != expected_key:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        inbox_id = data.get("inbox_id")
        reply_text = data.get("reply_text", "").strip()
        auto_send = data.get("auto_send", False)

        if not inbox_id or not reply_text:
            return JSONResponse({"error": "inbox_id and reply_text are required"}, status_code=400)

        try:
            inbox_id = int(inbox_id)
        except (TypeError, ValueError):
            return JSONResponse({"error": "Invalid inbox_id"}, status_code=400)

        with session_scope() as session:
            account = require_account(session, account_slug)
            item = session.scalar(
                select(CommentInbox).where(
                    CommentInbox.id == inbox_id,
                    CommentInbox.account_id == account.id,
                )
            )
            if not item:
                return JSONResponse({"error": "Comment not found"}, status_code=404)

            item.final_reply = reply_text
            if auto_send and item.can_transition_to(CommentInbox.STATUS_SENDING):
                item.status = CommentInbox.STATUS_APPROVED
                session.flush()
                summary = send_selected_comments(session, [item.id])
                return JSONResponse({
                    "success": True,
                    "inbox_id": inbox_id,
                    "sent": summary.get("sent", 0),
                    "failed": summary.get("failed", 0),
                })

        return JSONResponse({"success": True, "inbox_id": inbox_id, "status": "drafted"})
