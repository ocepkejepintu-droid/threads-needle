from __future__ import annotations

from datetime import datetime, timezone

from threads_analytics import config
from threads_analytics.db import session_scope
from threads_analytics.leads import send_reply
from threads_analytics.models import GeneratedIdea, Lead, LeadSource
from threads_analytics.publisher import publish_scheduled_idea
from threads_analytics.threads_client import ThreadsClient

pytest_plugins = ["tests.fixtures_accounts"]


def _set_default_threads_env(monkeypatch) -> None:
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "default-token")
    monkeypatch.setenv("THREADS_USER_ID", "default-user")
    monkeypatch.setenv("THREADS_HANDLE", "default-handle")
    config.get_settings.cache_clear()


def _create_scheduled_idea(account_id: int, concept: str) -> int:
    with session_scope() as session:
        idea = GeneratedIdea(
            account_id=account_id,
            title=f"Idea {account_id}",
            concept=concept,
            status="approved",
            scheduled_at=datetime.now(timezone.utc),
        )
        session.add(idea)
        session.flush()
        return idea.id


def _create_approved_lead(account_id: int, reply_text: str, thread_id: str) -> int:
    with session_scope() as session:
        source = LeadSource(
            account_id=account_id,
            name=f"Source {account_id}",
            keywords=["ai"],
        )
        session.add(source)
        session.flush()

        lead = Lead(
            account_id=account_id,
            source_id=source.id,
            thread_id=thread_id,
            author_username=f"author-{account_id}",
            author_user_id=f"author-user-{account_id}",
            author_bio="builder",
            post_text="How are people structuring their Threads workflow for AI tooling?",
            post_permalink=f"https://threads.net/{thread_id}",
            post_created_at=datetime.now(timezone.utc),
            matched_keyword="ai",
            status="approved",
            ai_draft_reply=reply_text,
        )
        session.add(lead)
        session.flush()
        return lead.id


def test_publish_and_reply_use_account_scoped_credentials(monkeypatch, account_a):
    _set_default_threads_env(monkeypatch)

    calls: list[tuple[str, str, str, str]] = []

    def fake_create_text_post(self, text: str, image_url: str | None = None):
        calls.append(("post", self.access_token, self.user_id, text))
        assert image_url is None
        return {"id": "published-account-a"}

    def fake_create_reply(self, reply_to_id: str, text: str):
        calls.append(("reply", self.access_token, self.user_id, reply_to_id))
        assert text == "Helpful reply for account A"
        return {"id": "reply-account-a"}

    monkeypatch.setattr(ThreadsClient, "create_text_post", fake_create_text_post)
    monkeypatch.setattr(ThreadsClient, "create_reply", fake_create_reply)

    from threads_analytics.publish_gate import GateResult
    import threads_analytics.publisher as pub
    monkeypatch.setattr(pub, "gate_publish_idea", lambda idea_id: GateResult(allowed=True))

    idea_id = _create_scheduled_idea(account_a.id, "Account A publish body")
    lead_id = _create_approved_lead(account_a.id, "Helpful reply for account A", "lead-thread-a")

    assert publish_scheduled_idea(idea_id) is True

    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead is not None
        assert send_reply(session, lead) is True

    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        lead = session.get(Lead, lead_id)
        assert idea is not None
        assert lead is not None
        assert idea.status == "published"
        assert idea.thread_id == "published-account-a"
        assert lead.status == "sent"

    assert calls == [
        ("post", "token-a", "user-a", "Account A publish body"),
        ("reply", "token-a", "user-a", "lead-thread-a"),
    ]


def test_expired_account_a_token_does_not_affect_account_b(monkeypatch, account_a, account_b):
    _set_default_threads_env(monkeypatch)

    with session_scope() as session:
        stored_a = session.get(type(account_a), account_a.id)
        assert stored_a is not None
        stored_a.threads_access_token = "expired-token-a"

    calls: list[tuple[str, str, str]] = []

    def fake_create_text_post(self, text: str, image_url: str | None = None):
        calls.append(("post", self.access_token, self.user_id))
        if self.access_token == "expired-token-a":
            raise RuntimeError("expired token")
        return {"id": f"published-{self.user_id}"}

    def fake_create_reply(self, reply_to_id: str, text: str):
        calls.append(("reply", self.access_token, self.user_id))
        if self.access_token == "expired-token-a":
            raise RuntimeError("expired token")
        return {"id": f"reply-{self.user_id}"}

    monkeypatch.setattr(ThreadsClient, "create_text_post", fake_create_text_post)
    monkeypatch.setattr(ThreadsClient, "create_reply", fake_create_reply)

    from threads_analytics.publish_gate import GateResult
    import threads_analytics.publisher as pub
    monkeypatch.setattr(pub, "gate_publish_idea", lambda idea_id: GateResult(allowed=True))

    idea_a_id = _create_scheduled_idea(account_a.id, "Account A publish body")
    idea_b_id = _create_scheduled_idea(account_b.id, "Account B publish body")
    lead_a_id = _create_approved_lead(account_a.id, "Reply from account A", "lead-thread-a")
    lead_b_id = _create_approved_lead(account_b.id, "Reply from account B", "lead-thread-b")

    assert publish_scheduled_idea(idea_a_id) is False
    assert publish_scheduled_idea(idea_b_id) is True

    with session_scope() as session:
        lead_a = session.get(Lead, lead_a_id)
        lead_b = session.get(Lead, lead_b_id)
        assert lead_a is not None
        assert lead_b is not None
        assert send_reply(session, lead_a) is False
        assert send_reply(session, lead_b) is True

    with session_scope() as session:
        idea_a = session.get(GeneratedIdea, idea_a_id)
        idea_b = session.get(GeneratedIdea, idea_b_id)
        lead_a = session.get(Lead, lead_a_id)
        lead_b = session.get(Lead, lead_b_id)
        assert idea_a is not None
        assert idea_b is not None
        assert lead_a is not None
        assert lead_b is not None

        assert idea_a.status == "failed"
        assert "expired token" in (idea_a.error_message or "")
        assert idea_b.status == "published"
        assert idea_b.thread_id == "published-user-b"

        assert lead_a.status == "approved"
        assert lead_a.sent_at is None
        assert lead_b.status == "sent"

    assert calls == [
        ("post", "expired-token-a", "user-a"),
        ("post", "token-b", "user-b"),
        ("reply", "expired-token-a", "user-a"),
        ("reply", "token-b", "user-b"),
    ]
