from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from threads_analytics.db import session_scope
from threads_analytics.models import GeneratedIdea, Lead, LeadSource
from threads_analytics.scheduler import _claim_due_posts, _run_account_posts, _run_account_replies

pytest_plugins = ["tests.fixtures_accounts"]


def test_claim_prevents_duplicate_execution(account_a):
    with session_scope() as session:
        idea = GeneratedIdea(
            account_id=account_a.id,
            title="Claim test",
            concept="Claim test body",
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(idea)
        session.flush()
        idea_id = idea.id

    claimed1 = _claim_due_posts()
    assert idea_id in claimed1

    # Second claim should return empty because claim token is already set
    claimed2 = _claim_due_posts()
    assert idea_id not in claimed2


def test_claim_resets_after_timeout(account_a):
    with session_scope() as session:
        idea = GeneratedIdea(
            account_id=account_a.id,
            title="Timeout test",
            concept="Timeout test body",
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            claim_token="old-token",
            claimed_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        session.add(idea)
        session.flush()
        idea_id = idea.id

    claimed = _claim_due_posts()
    assert idea_id in claimed

    with session_scope() as session:
        idea = session.get(GeneratedIdea, idea_id)
        assert idea.claim_token != "old-token"


def test_account_isolation_posts_and_replies(account_a, account_b, monkeypatch):
    with session_scope() as session:
        source_a = LeadSource(account_id=account_a.id, name="Src A", keywords=["ai"])
        source_b = LeadSource(account_id=account_b.id, name="Src B", keywords=["ai"])
        session.add_all([source_a, source_b])
        session.flush()

        idea_a = GeneratedIdea(
            account_id=account_a.id,
            title="A",
            concept="A specific hook with $500 data",
            status="approved",
            predicted_score=80,
        )
        idea_b = GeneratedIdea(
            account_id=account_b.id,
            title="B",
            concept="B specific hook with 20% growth",
            status="approved",
            predicted_score=80,
        )
        lead_a = Lead(
            account_id=account_a.id,
            source_id=source_a.id,
            thread_id="t-a",
            author_username="u-a",
            author_user_id="uid-a",
            post_text="post a",
            post_permalink="https://threads.net/t-a",
            post_created_at=datetime.now(timezone.utc),
            matched_keyword="ai",
            status="approved",
            final_reply="reply a",
        )
        lead_b = Lead(
            account_id=account_b.id,
            source_id=source_b.id,
            thread_id="t-b",
            author_username="u-b",
            author_user_id="uid-b",
            post_text="post b",
            post_permalink="https://threads.net/t-b",
            post_created_at=datetime.now(timezone.utc),
            matched_keyword="ai",
            status="approved",
            final_reply="reply b",
        )
        session.add_all([idea_a, idea_b, lead_a, lead_b])
        session.flush()

        # Claim items so scheduler will process them
        idea_a.claim_token = "claim-a"
        idea_b.claim_token = "claim-b"
        lead_a.claim_token = "claim-c"
        lead_b.claim_token = "claim-d"

    calls: list[tuple[str, int]] = []

    import threads_analytics.scheduler as sched_mod
    from threads_analytics.publish_gate import GateResult

    def fake_publish(idea_id: int) -> bool:
        calls.append(("post", idea_id))
        with session_scope() as session:
            idea = session.get(GeneratedIdea, idea_id)
            if idea:
                idea.status = "published"
                idea.thread_id = f"published-{idea_id}"
        return True

    def fake_send_reply(session, lead) -> bool:
        calls.append(("reply", lead.id))
        lead.status = "sent"
        lead.sent_at = datetime.now(timezone.utc)
        return True

    monkeypatch.setattr(sched_mod, "publish_scheduled_idea", fake_publish)
    monkeypatch.setattr(sched_mod, "gate_publish_idea", lambda idea_id: GateResult(allowed=True))
    monkeypatch.setattr(sched_mod, "send_reply", fake_send_reply)
    monkeypatch.setattr(sched_mod, "gate_send_reply", lambda lead_id: GateResult(allowed=True))

    # Run for both accounts
    _run_account_posts(account_a.id)
    _run_account_posts(account_b.id)
    _run_account_replies(account_a.id)
    _run_account_replies(account_b.id)

    post_calls = [c for c in calls if c[0] == "post"]
    reply_calls = [c for c in calls if c[0] == "reply"]

    assert len(post_calls) == 2
    assert len(reply_calls) == 2

    # Verify account isolation: each account got its own items
    with session_scope() as session:
        a_items = session.scalars(
            select(GeneratedIdea).where(GeneratedIdea.account_id == account_a.id)
        ).all()
        b_items = session.scalars(
            select(GeneratedIdea).where(GeneratedIdea.account_id == account_b.id)
        ).all()
        assert all(i.status == "published" for i in a_items)
        assert all(i.status == "published" for i in b_items)
