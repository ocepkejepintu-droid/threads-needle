from __future__ import annotations

# pyright: reportMissingImports=false, reportMissingTypeStubs=false, reportUnknownVariableType=false, reportAny=false

from datetime import datetime, timedelta, timezone
from typing import Final, cast

import pytest
from sqlalchemy import DateTime, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from threads_analytics.comment_inbox import (
    bulk_approve_comments,
    edit_comment_reply,
    poll_for_comments,
    send_selected_comments,
)
from threads_analytics.comment_reply_drafts import draft_replies_for_inbox
from threads_analytics.models import (
    Account,
    Base,
    CommentInbox,
    MyPost,
    MyReply,
    PublishLedger,
    Run,
    YouProfile,
)
from threads_analytics.publish_gate import GateResult
from threads_analytics.threads_client import PostComment
from threads_analytics.web.routes_comments import _load_scoped_item, _validate_scoped_ids


def _build_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_account_and_run(session: Session) -> Run:
    account = Account(slug="default", name="Default")
    session.add(account)
    session.flush()

    run = Run(account_id=account.id)
    session.add(run)
    session.commit()
    return run


def _seed_post(
    session: Session,
    *,
    account_id: int,
    run_id: int,
    thread_id: str,
    text: str,
    created_at: datetime,
) -> MyPost:
    post = MyPost(
        account_id=account_id,
        thread_id=thread_id,
        text=text,
        created_at=created_at,
        first_seen_run_id=run_id,
    )
    session.add(post)
    session.commit()
    return post


class StubThreadsClient:
    replies_by_post: dict[str, list[PostComment]]

    def __init__(self, replies_by_post: dict[str, list[PostComment]]):
        self.replies_by_post = replies_by_post

    def list_post_replies(self, post_thread_id: str, limit: int | None = 25) -> list[PostComment]:
        replies = list(self.replies_by_post.get(post_thread_id, []))
        if limit is None:
            return replies
        return replies[:limit]


class StubLLMResponse:
    text: str

    def __init__(self, text: str):
        self.text = text


class RecordingLLMClient:
    text: str

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict[str, object]] = []

    def create_message(self, **kwargs: object) -> StubLLMResponse:
        self.calls.append(kwargs)
        return StubLLMResponse(self.text)


class RecordingReplyClient:
    should_fail: bool

    def __init__(self, *, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls: list[tuple[str, str]] = []

    def create_reply(self, reply_to_id: str, text: str) -> dict[str, str]:
        self.calls.append((reply_to_id, text))
        if self.should_fail:
            raise RuntimeError("Threads publish exploded")
        return {"id": f"published-{reply_to_id}", "creation_id": f"creation-{reply_to_id}"}


def _seed_inbox_item(
    session: Session,
    *,
    run_id: int,
    account_id: int,
    comment_thread_id: str,
    status: str = CommentInbox.STATUS_DRAFTED,
    final_reply: str | None = None,
    ai_draft_reply: str | None = "Draft reply",
) -> CommentInbox:
    now = datetime.now(timezone.utc)
    item = CommentInbox(
        account_id=account_id,
        source_post_thread_id=f"source-{comment_thread_id}",
        source_post_text="Source post",
        source_post_created_at=now - timedelta(days=1),
        comment_thread_id=comment_thread_id,
        comment_permalink=f"https://example.com/comment/{comment_thread_id}",
        comment_author_username="reader",
        comment_author_user_id=f"user-{comment_thread_id}",
        comment_text="Can you say more?",
        comment_created_at=now,
        status=status,
        ai_draft_reply=ai_draft_reply,
        final_reply=final_reply,
        approved_at=now if status == CommentInbox.STATUS_APPROVED else None,
        first_seen_run_id=run_id,
        last_seen_at=now,
    )
    session.add(item)
    session.commit()
    return item


def test_schema_and_state_machine():
    session = _build_session()
    run = _seed_account_and_run(session)

    now = datetime.now(timezone.utc)
    inbox_item = CommentInbox(
        account_id=1,
        source_post_thread_id="post_1",
        source_post_text="Source post",
        source_post_created_at=now,
        comment_thread_id="comment_1",
        comment_permalink="https://www.threads.net/@acct/post/1/comment/1",
        comment_author_username="replyguy",
        comment_author_user_id="user_1",
        comment_text="Can you say more?",
        comment_created_at=now,
        ai_draft_reply="Absolutely — here's the draft.",
        ai_draft_generated_at=now,
        first_seen_run_id=run.id,
        last_seen_at=now,
    )
    session.add(inbox_item)
    session.commit()

    table_name = "comment_inbox"

    assert session.bind is not None
    inspector = inspect(session.bind)
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    unique_constraints = inspector.get_unique_constraints(table_name)

    assert CommentInbox.__tablename__ == table_name
    assert set(columns) == {
        "id",
        "account_id",
        "source_post_thread_id",
        "source_post_text",
        "source_post_created_at",
        "comment_thread_id",
        "comment_permalink",
        "comment_author_username",
        "comment_author_user_id",
        "comment_text",
        "comment_created_at",
        "status",
        "ai_draft_reply",
        "final_reply",
        "ai_draft_generated_at",
        "approved_at",
        "sent_at",
        "send_error",
        "published_reply_thread_id",
        "first_seen_run_id",
        "last_seen_at",
        "claim_token",
        "claimed_at",
    }
    assert CommentInbox.VALID_STATUSES == (
        "drafted",
        "approved",
        "sending",
        "sent",
        "send_failed",
        "ignored",
    )
    assert CommentInbox.can_transition("drafted", "approved") is True
    assert CommentInbox.can_transition("approved", "sending") is True
    assert CommentInbox.can_transition("approved", "sent") is False
    assert CommentInbox.can_transition("sent", "drafted") is False
    assert inbox_item.status == CommentInbox.STATUS_DRAFTED
    assert any(
        constraint["column_names"] == ["account_id", "comment_thread_id"]
        for constraint in unique_constraints
    )

    for name in (
        "source_post_created_at",
        "comment_created_at",
        "ai_draft_generated_at",
        "approved_at",
        "sent_at",
        "last_seen_at",
        "claimed_at",
    ):
        column_type = cast(DateTime, CommentInbox.__table__.c[name].type)
        assert isinstance(column_type, DateTime)
        assert column_type.timezone is True

    duplicate = CommentInbox(
        account_id=1,
        source_post_thread_id="post_1",
        source_post_text="Source post",
        source_post_created_at=now,
        comment_thread_id="comment_1",
        comment_author_username="replyguy",
        comment_author_user_id="user_1",
        comment_text="Same comment again",
        comment_created_at=now,
        first_seen_run_id=run.id,
        last_seen_at=now,
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()


def test_approval_boundary_state_machine():
    session = _build_session()
    run = _seed_account_and_run(session)

    now = datetime.now(timezone.utc)
    inbox_item = CommentInbox(
        account_id=1,
        source_post_thread_id="post_2",
        source_post_text="Another source post",
        source_post_created_at=now,
        comment_thread_id="comment_2",
        comment_author_username="reader",
        comment_author_user_id="user_2",
        comment_text="Ship the reply when ready",
        comment_created_at=now,
        status=CommentInbox.STATUS_APPROVED,
        ai_draft_reply="Thanks — here's what I mean.",
        final_reply="Thanks — here's what I mean.",
        ai_draft_generated_at=now,
        approved_at=now,
        first_seen_run_id=run.id,
        last_seen_at=now,
    )
    session.add(inbox_item)
    session.commit()

    approved = session.scalar(
        select(CommentInbox).where(CommentInbox.comment_thread_id == "comment_2")
    )
    assert approved is not None
    assert approved.status == CommentInbox.STATUS_APPROVED
    assert approved.sent_at is None
    assert approved.published_reply_thread_id is None
    assert approved.can_transition_to(CommentInbox.STATUS_SENT) is False
    assert approved.can_transition_to(CommentInbox.STATUS_SENDING) is True

    approved.status = CommentInbox.STATUS_SENDING
    session.commit()
    approved.status = CommentInbox.STATUS_SENT
    approved.sent_at = datetime.now(timezone.utc)
    approved.published_reply_thread_id = "reply_2"
    session.commit()

    sent = session.scalar(select(CommentInbox).where(CommentInbox.id == approved.id))
    assert sent is not None
    assert sent.status == CommentInbox.STATUS_SENT
    assert sent.sent_at is not None
    assert sent.published_reply_thread_id == "reply_2"


def test_poll_deduplicates_external_reply_id():
    session = _build_session()
    run = _seed_account_and_run(session)
    post = _seed_post(
        session,
        account_id=run.account_id,
        run_id=run.id,
        thread_id="post_recent",
        text="Recent post",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    client = StubThreadsClient(
        {
            post.thread_id: [
                PostComment(
                    id="17890001",
                    text="First pass",
                    username="reader",
                    user_id="user_1",
                    permalink="https://example.com/comment/17890001",
                    created_at=datetime.now(timezone.utc),
                )
            ]
        }
    )

    first = poll_for_comments(session, client, run.account_id, run.id)
    second = poll_for_comments(session, client, run.account_id, run.id)
    session.commit()

    rows = session.scalars(select(CommentInbox)).all()
    assert first == {
        "posts_scanned": 1,
        "comments_found": 1,
        "comments_inserted": 1,
        "comments_updated": 0,
        "comments_skipped_operator": 0,
        "comments_skipped_replied": 0,
    }
    assert second == {
        "posts_scanned": 1,
        "comments_found": 1,
        "comments_inserted": 0,
        "comments_updated": 1,
        "comments_skipped_operator": 0,
        "comments_skipped_replied": 0,
    }
    assert len(rows) == 1
    assert rows[0].comment_thread_id == "17890001"


def test_poll_scans_all_posts_regardless_of_age():
    session = _build_session()
    run = _seed_account_and_run(session)
    post = _seed_post(
        session,
        account_id=run.account_id,
        run_id=run.id,
        thread_id="post_old",
        text="Old but still scanned",
        created_at=datetime.now(timezone.utc) - timedelta(days=31),
    )
    client = StubThreadsClient(
        {
            post.thread_id: [
                PostComment(
                    id="17890002",
                    text="You should see me",
                    username="reader",
                    user_id="user_2",
                    permalink="https://example.com/comment/17890002",
                    created_at=datetime.now(timezone.utc),
                )
            ]
        }
    )

    summary = poll_for_comments(session, client, run.account_id, run.id)
    session.commit()

    assert summary == {
        "posts_scanned": 1,
        "comments_found": 1,
        "comments_inserted": 1,
        "comments_updated": 0,
        "comments_skipped_operator": 0,
        "comments_skipped_replied": 0,
    }
    assert len(session.scalars(select(CommentInbox)).all()) == 1


def test_poll_ingests_more_than_100_replies_via_pagination():
    session = _build_session()
    run = _seed_account_and_run(session)
    post = _seed_post(
        session,
        account_id=run.account_id,
        run_id=run.id,
        thread_id="post_viral",
        text="Viral post",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    class _PaginatedClient(StubThreadsClient):
        def __init__(self):
            self.calls: list[tuple[str, int | None]] = []

        def list_post_replies(self, post_thread_id: str, limit: int | None = 25):
            self.calls.append((post_thread_id, limit))
            return [
                PostComment(
                    id=f"c{i}",
                    text=f"comment {i}",
                    username="reader",
                    user_id="reader-id",
                    permalink=f"https://example.com/c{i}",
                    created_at=datetime.now(timezone.utc),
                )
                for i in range(150)
            ]

    client = _PaginatedClient()
    summary = poll_for_comments(session, client, run.account_id, run.id)
    session.commit()

    assert summary["posts_scanned"] == 1
    assert summary["comments_found"] == 150
    assert summary["comments_inserted"] == 150
    assert client.calls == [(post.thread_id, None)]


def test_poll_preserves_sent_status_on_update():
    session = _build_session()
    run = _seed_account_and_run(session)
    post = _seed_post(
        session,
        account_id=run.account_id,
        run_id=run.id,
        thread_id="post_sent",
        text="Source post",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    sent_at = datetime.now(timezone.utc) - timedelta(hours=1)
    session.add(
        CommentInbox(
            account_id=run.account_id,
            source_post_thread_id=post.thread_id,
            source_post_text=post.text,
            source_post_created_at=post.created_at,
            comment_thread_id="17890003",
            comment_permalink="https://example.com/old",
            comment_author_username="reader",
            comment_author_user_id="user_3",
            comment_text="Old text",
            comment_created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            status=CommentInbox.STATUS_SENT,
            ai_draft_reply="draft",
            final_reply="final",
            approved_at=sent_at - timedelta(minutes=10),
            sent_at=sent_at,
            send_error="old error should stay",
            published_reply_thread_id="reply_17890003",
            first_seen_run_id=run.id,
            last_seen_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
    )
    session.commit()

    client = StubThreadsClient(
        {
            post.thread_id: [
                PostComment(
                    id="17890003",
                    text="New text",
                    username="reader-updated",
                    user_id="user_3_updated",
                    permalink="https://example.com/new",
                    created_at=datetime.now(timezone.utc),
                )
            ]
        }
    )

    summary = poll_for_comments(session, client, run.account_id, run.id)
    session.commit()

    updated = session.scalar(
        select(CommentInbox).where(CommentInbox.comment_thread_id == "17890003")
    )
    assert summary == {
        "posts_scanned": 1,
        "comments_found": 1,
        "comments_inserted": 0,
        "comments_updated": 0,
        "comments_skipped_operator": 0,
        "comments_skipped_replied": 1,
    }
    assert updated is not None
    assert updated.status == CommentInbox.STATUS_SENT
    assert updated.comment_text == "Old text"
    assert updated.comment_permalink == "https://example.com/old"
    assert updated.comment_author_username == "reader"
    assert updated.comment_author_user_id == "user_3"
    assert updated.ai_draft_reply == "draft"
    assert updated.final_reply == "final"
    assert updated.approved_at == (sent_at - timedelta(minutes=10)).replace(tzinfo=None)
    assert updated.sent_at == sent_at.replace(tzinfo=None)
    assert updated.send_error == "old error should stay"
    assert updated.published_reply_thread_id == "reply_17890003"


def test_draft_generation_uses_you_profile_and_recent_replies(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)
    now = datetime.now(timezone.utc)

    session.add(
        YouProfile(
            account_id=run.account_id,
            run_id=run.id,
            core_identity="Builder-teacher who explains tradeoffs plainly.",
            distinctive_voice_traits=[
                {
                    "trait": "specific",
                    "evidence": "grounds advice in concrete details",
                    "example": "name the failure mode, then fix it",
                }
            ],
            stylistic_signatures=[
                {"signature": "short sentences, no fluff", "evidence": "recent posts"}
            ],
            posts_that_sound_most_like_you=[
                {
                    "post_id": "p_like_1",
                    "text": "Tight feedback loops beat big rewrites.",
                    "why": "signature framing",
                }
            ],
            created_at=now,
        )
    )
    session.add_all(
        [
            MyPost(
                account_id=run.account_id,
                thread_id="post_voice_1",
                text="Shipping small lets you see where the real complexity lives.",
                created_at=now - timedelta(days=1),
                first_seen_run_id=run.id,
            ),
            MyPost(
                account_id=run.account_id,
                thread_id="post_voice_2",
                text="Most dashboards fail because the question is fuzzy, not because the chart is wrong.",
                created_at=now - timedelta(days=2),
                first_seen_run_id=run.id,
            ),
            MyReply(
                account_id=run.account_id,
                thread_id="my_reply_1",
                text="I'd start by narrowing the scope and testing one path end to end.",
                created_at=now - timedelta(hours=3),
                root_post_id="root_1",
                first_seen_run_id=run.id,
            ),
            MyReply(
                account_id=run.account_id,
                thread_id="my_reply_2",
                text="The tricky part is usually state management, not the API call itself.",
                created_at=now - timedelta(hours=5),
                root_post_id="root_2",
                first_seen_run_id=run.id,
            ),
            CommentInbox(
                account_id=run.account_id,
                source_post_thread_id="source_1",
                source_post_text="Some source post",
                source_post_created_at=now - timedelta(days=1),
                comment_thread_id="reply_456",
                comment_permalink="https://example.com/comment/reply_456",
                comment_author_username="reader",
                comment_author_user_id="user_456",
                comment_text="How would you approach this if the API is flaky?",
                comment_created_at=now,
                first_seen_run_id=run.id,
                last_seen_at=now,
            ),
        ]
    )
    session.commit()

    client = RecordingLLMClient(
        "Start with the smallest reliable path, then add retries once you know where it actually fails."
    )
    monkeypatch.setattr("threads_analytics.comment_reply_drafts.get_llm_client", lambda: client)

    count = draft_replies_for_inbox(session, run.account_id)
    session.commit()

    updated = session.scalar(
        select(CommentInbox).where(CommentInbox.comment_thread_id == "reply_456")
    )
    assert count == 1
    assert updated is not None
    assert updated.ai_draft_reply is not None
    assert client.calls

    system_prompt = cast(str, client.calls[0]["system"])
    user_messages = cast(list[dict[str, str]], client.calls[0]["messages"])
    user_prompt = user_messages[0]["content"]

    assert "Builder-teacher who explains tradeoffs plainly." in system_prompt
    assert "I'd start by narrowing the scope and testing one path end to end." in system_prompt
    assert "The tricky part is usually state management, not the API call itself." in system_prompt
    assert "Shipping small lets you see where the real complexity lives." in system_prompt
    assert "How would you approach this if the API is flaky?" in user_prompt
    assert "@reader" in user_prompt


def test_redraft_preserves_final_reply(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)
    now = datetime.now(timezone.utc)

    session.add(
        CommentInbox(
            account_id=run.account_id,
            source_post_thread_id="source_2",
            source_post_text="Source post",
            source_post_created_at=now - timedelta(days=1),
            comment_thread_id="reply_preserve",
            comment_permalink="https://example.com/comment/reply_preserve",
            comment_author_username="reader",
            comment_author_user_id="user_preserve",
            comment_text="Could you share one concrete example?",
            comment_created_at=now,
            ai_draft_reply=None,
            final_reply="Thanks — appreciate it.",
            first_seen_run_id=run.id,
            last_seen_at=now,
        )
    )
    session.commit()

    expected_draft: Final[str] = (
        "One example: test the smallest useful slice before you generalize the solution."
    )
    client = RecordingLLMClient(expected_draft)
    monkeypatch.setattr("threads_analytics.comment_reply_drafts.get_llm_client", lambda: client)

    count = draft_replies_for_inbox(session, run.account_id)
    session.commit()

    updated = session.scalar(
        select(CommentInbox).where(CommentInbox.comment_thread_id == "reply_preserve")
    )
    assert count == 1
    assert updated is not None
    assert updated.ai_draft_reply == expected_draft
    assert updated.final_reply == "Thanks — appreciate it."


def test_bulk_approve_manual_send_boundary(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)

    account = session.get(Account, run.account_id)
    assert account is not None
    account.enabled_capabilities = ["reply"]
    account.threads_access_token = "token"
    account.threads_user_id = "user"
    session.commit()

    first = _seed_inbox_item(
        session, run_id=run.id, account_id=run.account_id, comment_thread_id="c1"
    )
    second = _seed_inbox_item(
        session, run_id=run.id, account_id=run.account_id, comment_thread_id="c2"
    )

    client = RecordingReplyClient()

    def fake_from_account(_account: object) -> RecordingReplyClient:
        return client

    monkeypatch.setattr(
        "threads_analytics.publish_gate.ThreadsClient.from_account", fake_from_account
    )

    approved = bulk_approve_comments(session, [first.id, second.id])
    session.commit()

    refreshed = session.scalars(
        select(CommentInbox)
        .where(CommentInbox.id.in_([first.id, second.id]))
        .order_by(CommentInbox.id)
    ).all()

    assert approved == 2
    assert [item.status for item in refreshed] == [
        CommentInbox.STATUS_APPROVED,
        CommentInbox.STATUS_APPROVED,
    ]
    assert all(item.approved_at is not None for item in refreshed)
    assert client.calls == []


def test_send_failure_preserves_approved_draft(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)

    account = session.get(Account, run.account_id)
    assert account is not None
    account.enabled_capabilities = ["reply"]
    account.threads_access_token = "token"
    account.threads_user_id = "user"
    session.add(
        YouProfile(
            account_id=run.account_id,
            run_id=run.id,
            core_identity="Plainspoken builder",
        )
    )
    session.commit()

    inbox_item = _seed_inbox_item(
        session,
        run_id=run.id,
        account_id=run.account_id,
        comment_thread_id="c-fail",
        status=CommentInbox.STATUS_APPROVED,
        final_reply="Keep the human-edited final reply intact.",
        ai_draft_reply="Fallback draft",
    )

    class SlopResult:
        passed: bool = True
        failures: list[str] = []

    class BrandResult:
        passed: bool = True
        suggestions: list[object] = []

    def always_pass_slop(_text: str) -> SlopResult:
        return SlopResult()

    def always_pass_brand(_text: str, _you: object) -> BrandResult:
        return BrandResult()

    def allow_gate(_inbox_id: int) -> GateResult:
        return GateResult(allowed=True)

    monkeypatch.setattr(
        "threads_analytics.publish_gate.content_rules.validate_content",
        always_pass_slop,
    )
    monkeypatch.setattr(
        "threads_analytics.publish_gate.brand_validate_content",
        always_pass_brand,
    )
    monkeypatch.setattr("threads_analytics.comment_inbox.gate_send_comment", allow_gate)

    failing_client = RecordingReplyClient(should_fail=True)
    summary = send_selected_comments(session, [inbox_item.id], client=failing_client)
    session.commit()

    refreshed = session.get(CommentInbox, inbox_item.id)
    ledger_rows = session.scalars(select(PublishLedger)).all()

    assert summary == {"sent": 0, "failed": 1, "skipped": 0}
    assert failing_client.calls == [("c-fail", "Keep the human-edited final reply intact.")]
    assert refreshed is not None
    assert refreshed.status == CommentInbox.STATUS_SEND_FAILED
    assert refreshed.final_reply == "Keep the human-edited final reply intact."
    assert refreshed.send_error
    assert refreshed.sent_at is None
    assert refreshed.published_reply_thread_id is None
    assert ledger_rows == []


def test_quota_guard_blocks_over_limit(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)

    account = session.get(Account, run.account_id)
    assert account is not None
    account.enabled_capabilities = ["reply"]
    account.soft_caps = {"replies_per_day": 1}
    account.threads_access_token = "token"
    account.threads_user_id = "user"
    session.commit()

    first = _seed_inbox_item(
        session,
        run_id=run.id,
        account_id=run.account_id,
        comment_thread_id="quota-1",
        status=CommentInbox.STATUS_APPROVED,
        final_reply="First reply",
    )
    second = _seed_inbox_item(
        session,
        run_id=run.id,
        account_id=run.account_id,
        comment_thread_id="quota-2",
        status=CommentInbox.STATUS_APPROVED,
        final_reply="Second reply",
    )

    class SlopResult:
        passed: bool = True
        failures: list[str] = []

    class _SessionScope:
        def __enter__(self) -> Session:
            return session

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

    def always_pass_slop(_text: str) -> SlopResult:
        return SlopResult()

    monkeypatch.setattr(
        "threads_analytics.publish_gate.content_rules.validate_content",
        always_pass_slop,
    )
    monkeypatch.setattr("threads_analytics.publish_gate.session_scope", _SessionScope)
    monkeypatch.setattr(
        "threads_analytics.publish_gate.ThreadsClient.from_account",
        lambda _account: RecordingReplyClient(),
    )

    summary = send_selected_comments(
        session,
        [first.id, second.id],
        client=RecordingReplyClient(),
    )
    session.commit()

    refreshed_first = session.get(CommentInbox, first.id)
    refreshed_second = session.get(CommentInbox, second.id)

    assert summary == {"sent": 1, "failed": 1, "skipped": 0}
    assert refreshed_first is not None
    assert refreshed_first.status == CommentInbox.STATUS_SENT
    assert refreshed_first.sent_at is not None
    assert refreshed_second is not None
    assert refreshed_second.status == CommentInbox.STATUS_SEND_FAILED
    assert refreshed_second.send_error is not None
    assert "quota" in refreshed_second.send_error.lower()


def test_nested_reply_handling():
    session = _build_session()
    run = _seed_account_and_run(session)
    post = _seed_post(
        session,
        account_id=run.account_id,
        run_id=run.id,
        thread_id="post_nested",
        text="Mission control source post",
        created_at=datetime.now(timezone.utc) - timedelta(hours=6),
    )

    class RecordingPollingClient(StubThreadsClient):
        def __init__(self, replies_by_post: dict[str, list[PostComment]]):
            super().__init__(replies_by_post)
            self.calls: list[tuple[str, int]] = []

        def list_post_replies(
            self, post_thread_id: str, limit: int | None = 25
        ) -> list[PostComment]:
            self.calls.append((post_thread_id, limit))
            return super().list_post_replies(post_thread_id, limit)

    client = RecordingPollingClient(
        {
            post.thread_id: [
                PostComment(
                    id="top-level-1",
                    text="Top-level comment",
                    username="reader_one",
                    user_id="user_one",
                    permalink="https://example.com/comment/top-level-1",
                    created_at=datetime.now(timezone.utc),
                ),
                PostComment(
                    id="nested-looking-2",
                    text="Nested-looking reply payload",
                    username="reader_two",
                    user_id="user_two",
                    permalink="https://example.com/comment/nested-looking-2",
                    created_at=datetime.now(timezone.utc),
                ),
            ]
        }
    )

    summary = poll_for_comments(session, client, run.account_id, run.id)
    session.commit()

    rows = session.scalars(select(CommentInbox).order_by(CommentInbox.comment_thread_id)).all()

    assert summary == {
        "posts_scanned": 1,
        "comments_found": 2,
        "comments_inserted": 2,
        "comments_updated": 0,
        "comments_skipped_operator": 0,
        "comments_skipped_replied": 0,
    }
    assert client.calls == [(post.thread_id, None)]
    assert [row.comment_thread_id for row in rows] == ["nested-looking-2", "top-level-1"]


def test_cross_account_isolation():
    session = _build_session()
    run_a = _seed_account_and_run(session)

    account_b = Account(slug="second", name="Second")
    session.add(account_b)
    session.flush()

    run_b = Run(account_id=account_b.id)
    session.add(run_b)
    session.commit()

    inbox_b = _seed_inbox_item(
        session,
        run_id=run_b.id,
        account_id=account_b.id,
        comment_thread_id="account-b-item",
        status=CommentInbox.STATUS_DRAFTED,
        final_reply="Account B draft",
    )

    scoped_for_approve = _validate_scoped_ids(session, run_a.account_id, [inbox_b.id])
    approved = bulk_approve_comments(
        session,
        [] if scoped_for_approve is None else [item.id for item in scoped_for_approve],
    )

    scoped_for_send = _validate_scoped_ids(session, run_a.account_id, [inbox_b.id])
    send_summary = send_selected_comments(
        session,
        [] if scoped_for_send is None else [item.id for item in scoped_for_send],
        client=RecordingReplyClient(),
    )

    scoped_for_edit = _load_scoped_item(session, run_a.account_id, inbox_b.id)
    edited = None
    if scoped_for_edit is not None:
        edited = edit_comment_reply(session, scoped_for_edit.id, "Account A should not edit this")
    session.commit()

    refreshed = session.get(CommentInbox, inbox_b.id)

    assert scoped_for_approve is None
    assert approved == 0
    assert scoped_for_send is None
    assert send_summary == {"sent": 0, "failed": 0, "skipped": 0}
    assert scoped_for_edit is None
    assert edited is None
    assert refreshed is not None
    assert refreshed.account_id == account_b.id
    assert refreshed.status == CommentInbox.STATUS_DRAFTED
    assert refreshed.final_reply == "Account B draft"
    assert refreshed.approved_at is None
    assert refreshed.sent_at is None
    assert refreshed.published_reply_thread_id is None


def test_ignore_persists_through_repoll():
    session = _build_session()
    run = _seed_account_and_run(session)
    post = _seed_post(
        session,
        account_id=run.account_id,
        run_id=run.id,
        thread_id="post_ignore",
        text="Source post",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    first_client = StubThreadsClient(
        {
            post.thread_id: [
                PostComment(
                    id="ignored-comment",
                    text="Original text",
                    username="reader",
                    user_id="user-ignore",
                    permalink="https://example.com/comment/ignored-comment-v1",
                    created_at=datetime.now(timezone.utc),
                )
            ]
        }
    )
    poll_for_comments(session, first_client, run.account_id, run.id)
    session.commit()

    inbox_item = session.scalar(
        select(CommentInbox).where(CommentInbox.comment_thread_id == "ignored-comment")
    )
    assert inbox_item is not None
    inbox_item.status = CommentInbox.STATUS_IGNORED
    session.commit()

    second_client = StubThreadsClient(
        {
            post.thread_id: [
                PostComment(
                    id="ignored-comment",
                    text="Updated text after repoll",
                    username="reader-updated",
                    user_id="user-ignore-updated",
                    permalink="https://example.com/comment/ignored-comment-v2",
                    created_at=datetime.now(timezone.utc),
                )
            ]
        }
    )

    summary = poll_for_comments(session, second_client, run.account_id, run.id)
    session.commit()

    refreshed = session.get(CommentInbox, inbox_item.id)

    assert summary == {
        "posts_scanned": 1,
        "comments_found": 1,
        "comments_inserted": 0,
        "comments_updated": 1,
        "comments_skipped_operator": 0,
        "comments_skipped_replied": 0,
    }
    assert refreshed is not None
    assert refreshed.status == CommentInbox.STATUS_IGNORED
    assert refreshed.comment_text == "Updated text after repoll"
    assert refreshed.comment_permalink == "https://example.com/comment/ignored-comment-v2"


def test_send_failure_recovery(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)

    inbox_item = _seed_inbox_item(
        session,
        run_id=run.id,
        account_id=run.account_id,
        comment_thread_id="retry-comment",
        status=CommentInbox.STATUS_APPROVED,
        final_reply="Human-polished final reply",
        ai_draft_reply="Initial AI draft",
    )

    def allow_gate(_inbox_id: int) -> GateResult:
        return GateResult(allowed=True)

    class FlakyReplyClient:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self.attempts = 0

        def create_reply(self, reply_to_id: str, text: str) -> dict[str, str]:
            self.calls.append((reply_to_id, text))
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("Transient Threads failure")
            return {
                "id": f"published-{reply_to_id}",
                "creation_id": f"creation-{reply_to_id}",
            }

    monkeypatch.setattr("threads_analytics.comment_inbox.gate_send_comment", allow_gate)

    client = FlakyReplyClient()

    first_summary = send_selected_comments(session, [inbox_item.id], client=client)
    session.commit()

    failed = session.get(CommentInbox, inbox_item.id)
    assert failed is not None
    assert first_summary == {"sent": 0, "failed": 1, "skipped": 0}
    assert failed.status == CommentInbox.STATUS_SEND_FAILED
    assert failed.send_error == "Transient Threads failure"
    assert failed.final_reply == "Human-polished final reply"
    assert failed.sent_at is None
    assert failed.published_reply_thread_id is None

    reapproved = bulk_approve_comments(session, [inbox_item.id])
    session.commit()

    retried_summary = send_selected_comments(session, [inbox_item.id], client=client)
    session.commit()

    refreshed = session.get(CommentInbox, inbox_item.id)
    ledger_rows = session.scalars(select(PublishLedger)).all()

    assert reapproved == 1
    assert retried_summary == {"sent": 1, "failed": 0, "skipped": 0}
    assert refreshed is not None
    assert refreshed.status == CommentInbox.STATUS_SENT
    assert refreshed.send_error is None
    assert refreshed.final_reply == "Human-polished final reply"
    assert refreshed.sent_at is not None
    assert refreshed.published_reply_thread_id == "published-retry-comment"
    assert client.calls == [
        ("retry-comment", "Human-polished final reply"),
        ("retry-comment", "Human-polished final reply"),
    ]
    assert len(ledger_rows) == 1
    assert ledger_rows[0].thread_id == "published-retry-comment"


def test_regenerate_selected_drafts(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)
    now = datetime.now(timezone.utc)

    client = RecordingLLMClient("Regenerated draft text")
    monkeypatch.setattr("threads_analytics.comment_reply_drafts.get_llm_client", lambda: client)

    item_no_draft = CommentInbox(
        account_id=run.account_id,
        source_post_thread_id="src-no-draft",
        source_post_text="Source post",
        source_post_created_at=now - timedelta(days=1),
        comment_thread_id="no-draft",
        comment_permalink="https://example.com/no-draft",
        comment_author_username="reader",
        comment_author_user_id="user-no-draft",
        comment_text="First comment",
        comment_created_at=now,
        status=CommentInbox.STATUS_DRAFTED,
        ai_draft_reply=None,
        final_reply=None,
        first_seen_run_id=run.id,
        last_seen_at=now,
    )
    item_old_draft = CommentInbox(
        account_id=run.account_id,
        source_post_thread_id="src-old-draft",
        source_post_text="Source post",
        source_post_created_at=now - timedelta(days=1),
        comment_thread_id="old-draft",
        comment_permalink="https://example.com/old-draft",
        comment_author_username="reader",
        comment_author_user_id="user-old-draft",
        comment_text="Second comment",
        comment_created_at=now,
        status=CommentInbox.STATUS_DRAFTED,
        ai_draft_reply="Old draft",
        final_reply=None,
        first_seen_run_id=run.id,
        last_seen_at=now,
    )
    session.add_all([item_no_draft, item_old_draft])
    session.commit()

    drafted = draft_replies_for_inbox(
        session,
        run.account_id,
        inbox_ids=[item_no_draft.id, item_old_draft.id],
        force_regenerate=True,
    )
    session.commit()

    assert drafted == 2
    refreshed_no = session.get(CommentInbox, item_no_draft.id)
    refreshed_old = session.get(CommentInbox, item_old_draft.id)
    assert refreshed_no is not None
    assert refreshed_old is not None
    assert refreshed_no.ai_draft_reply == "Regenerated draft text"
    assert refreshed_old.ai_draft_reply == "Regenerated draft text"
    assert refreshed_no.final_reply is None
    assert refreshed_old.final_reply is None


def test_regenerate_selected_preserves_final_reply(monkeypatch: pytest.MonkeyPatch):
    session = _build_session()
    run = _seed_account_and_run(session)
    now = datetime.now(timezone.utc)

    client = RecordingLLMClient("New regenerated draft")
    monkeypatch.setattr("threads_analytics.comment_reply_drafts.get_llm_client", lambda: client)

    item = CommentInbox(
        account_id=run.account_id,
        source_post_thread_id="src-preserve",
        source_post_text="Source post",
        source_post_created_at=now - timedelta(days=1),
        comment_thread_id="preserve",
        comment_permalink="https://example.com/preserve",
        comment_author_username="reader",
        comment_author_user_id="user-preserve",
        comment_text="Can you explain?",
        comment_created_at=now,
        status=CommentInbox.STATUS_DRAFTED,
        ai_draft_reply="Old draft",
        final_reply="User edited",
        first_seen_run_id=run.id,
        last_seen_at=now,
    )
    session.add(item)
    session.commit()

    drafted = draft_replies_for_inbox(
        session, run.account_id, inbox_ids=[item.id], force_regenerate=True
    )
    session.commit()

    assert drafted == 1
    refreshed = session.get(CommentInbox, item.id)
    assert refreshed is not None
    assert refreshed.ai_draft_reply == "New regenerated draft"
    assert refreshed.final_reply == "User edited"
