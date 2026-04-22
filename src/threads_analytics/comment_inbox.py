"""Polling ingestion and workflow actions for inbound post comments."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, CommentInbox, MyPost, PublishLedger
from .publish_gate import gate_send_comment
from .threads_client import PostComment

if TYPE_CHECKING:
    from .threads_client import ThreadsClient

log = logging.getLogger(__name__)


class CommentPollingClient(Protocol):
    def list_post_replies(
        self, post_thread_id: str, limit: int | None = 25
    ) -> list[PostComment]: ...


def edit_comment_reply(session: Session, inbox_id: int, new_text: str) -> CommentInbox | None:
    inbox_item = session.get(CommentInbox, inbox_id)
    if inbox_item is None:
        return None

    inbox_item.final_reply = new_text
    if inbox_item.status == CommentInbox.STATUS_APPROVED:
        inbox_item.status = CommentInbox.STATUS_DRAFTED
        inbox_item.approved_at = None
    return inbox_item


def bulk_approve_comments(session: Session, inbox_ids: list[int]) -> int:
    if not inbox_ids:
        return 0

    now = datetime.now(timezone.utc)
    approved = 0
    for inbox_item in session.scalars(select(CommentInbox).where(CommentInbox.id.in_(inbox_ids))):
        if inbox_item.status in {
            CommentInbox.STATUS_DRAFTED,
            CommentInbox.STATUS_SEND_FAILED,
        }:
            inbox_item.status = CommentInbox.STATUS_APPROVED
            inbox_item.approved_at = now
            inbox_item.send_error = None
            approved += 1
    return approved


def bulk_unapprove_comments(session: Session, inbox_ids: list[int]) -> int:
    if not inbox_ids:
        return 0

    updated = 0
    for inbox_item in session.scalars(select(CommentInbox).where(CommentInbox.id.in_(inbox_ids))):
        if inbox_item.status == CommentInbox.STATUS_APPROVED:
            inbox_item.status = CommentInbox.STATUS_DRAFTED
            inbox_item.approved_at = None
            updated += 1
    return updated


def bulk_ignore_comments(session: Session, inbox_ids: list[int]) -> int:
    if not inbox_ids:
        return 0

    updated = 0
    for inbox_item in session.scalars(select(CommentInbox).where(CommentInbox.id.in_(inbox_ids))):
        if inbox_item.status != CommentInbox.STATUS_SENT:
            inbox_item.status = CommentInbox.STATUS_IGNORED
            inbox_item.approved_at = None
            updated += 1
    return updated


def send_selected_comments(
    session: Session,
    inbox_ids: list[int],
    client: "ThreadsClient | None" = None,
) -> dict[str, int]:
    summary = {"sent": 0, "failed": 0, "skipped": 0}
    if not inbox_ids:
        return summary

    items = list(session.scalars(select(CommentInbox).where(CommentInbox.id.in_(inbox_ids))))
    clients_by_account: dict[int, ThreadsClient] = {}

    for inbox_item in items:
        if inbox_item.status != CommentInbox.STATUS_APPROVED:
            summary["skipped"] += 1
            continue

        gate = gate_send_comment(inbox_item.id)
        if not gate.allowed:
            inbox_item.status = CommentInbox.STATUS_SEND_FAILED
            inbox_item.send_error = gate.reason or "Comment reply blocked by publish gate"
            summary["failed"] += 1
            continue

        reply_text = inbox_item.final_reply or inbox_item.ai_draft_reply
        if not reply_text:
            inbox_item.status = CommentInbox.STATUS_SEND_FAILED
            inbox_item.send_error = "No reply text available"
            summary["failed"] += 1
            continue

        account = session.get(Account, inbox_item.account_id)
        if account is None:
            inbox_item.status = CommentInbox.STATUS_SEND_FAILED
            inbox_item.send_error = "Account not found"
            summary["failed"] += 1
            continue

        try:
            inbox_item.status = CommentInbox.STATUS_SENDING
            inbox_item.send_error = None

            reply_client = client
            if reply_client is None:
                reply_client = clients_by_account.get(account.id)
                if reply_client is None:
                    from .threads_client import ThreadsClient

                    reply_client = ThreadsClient.from_account(account)
                    clients_by_account[account.id] = reply_client

            result = reply_client.create_reply(
                reply_to_id=inbox_item.comment_thread_id,
                text=reply_text,
            )
            now = datetime.now(timezone.utc)
            inbox_item.status = CommentInbox.STATUS_SENT
            inbox_item.sent_at = now
            inbox_item.send_error = None
            inbox_item.published_reply_thread_id = result.get("id")

            ledger = PublishLedger(
                account_id=inbox_item.account_id,
                source_type="comment_inbox",
                source_id=inbox_item.id,
                workflow_type="reply",
                creation_id=result.get("creation_id"),
                thread_id=result.get("id"),
                status="published",
            )
            session.add(ledger)
            summary["sent"] += 1
        except Exception as exc:
            log.error("Failed to send comment reply for inbox item %s: %s", inbox_item.id, exc)
            inbox_item.status = CommentInbox.STATUS_SEND_FAILED
            inbox_item.send_error = str(exc) or exc.__class__.__name__
            summary["failed"] += 1

    return summary


def poll_for_comments(
    session: Session,
    client: CommentPollingClient,
    account_id: int,
    run_id: int,
) -> dict[str, int]:
    """Ingest top-level comments into the reply workflow inbox.

    Skips:
    - Comments from the operator themselves (no self-replies).
    - Comments already marked as sent (already replied via workflow).
    """

    now = datetime.now(timezone.utc)
    account = session.get(Account, account_id)
    operator_handle = (account.threads_handle or "").lower() if account else ""

    posts = list(
        session.scalars(
            select(MyPost).where(MyPost.account_id == account_id).order_by(MyPost.created_at.desc())
        )
    )

    summary = {
        "posts_scanned": len(posts),
        "comments_found": 0,
        "comments_inserted": 0,
        "comments_updated": 0,
        "comments_skipped_operator": 0,
        "comments_skipped_replied": 0,
    }

    for post in posts:
        comments = client.list_post_replies(post.thread_id, limit=None) or []
        for comment in comments:
            summary["comments_found"] += 1

            # Skip operator's own comments
            if operator_handle and (comment.username or "").lower() == operator_handle:
                summary["comments_skipped_operator"] += 1
                continue

            existing = session.scalar(
                select(CommentInbox).where(
                    CommentInbox.account_id == account_id,
                    CommentInbox.comment_thread_id == comment.id,
                )
            )

            # Skip comments we already replied to via the workflow
            if existing is not None and existing.status == CommentInbox.STATUS_SENT:
                summary["comments_skipped_replied"] += 1
                continue

            if existing is None:
                session.add(
                    CommentInbox(
                        account_id=account_id,
                        source_post_thread_id=post.thread_id,
                        source_post_text=post.text,
                        source_post_created_at=post.created_at,
                        comment_thread_id=comment.id,
                        comment_text=comment.text,
                        comment_author_username=comment.username or "",
                        comment_author_user_id=comment.user_id or "",
                        comment_permalink=comment.permalink,
                        comment_created_at=comment.created_at,
                        first_seen_run_id=run_id,
                        last_seen_at=now,
                    )
                )
                summary["comments_inserted"] += 1
                continue

            existing.comment_text = comment.text
            existing.comment_permalink = comment.permalink
            existing.last_seen_at = now
            summary["comments_updated"] += 1

    return summary
