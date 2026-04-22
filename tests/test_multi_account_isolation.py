from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from threads_analytics.account_scope import get_scoped, scope_statement
from threads_analytics.db import session_scope
from threads_analytics.models import GeneratedIdea, MyPost, Run

pytest_plugins = ["tests.fixtures_accounts"]


def test_account_scoped_queries_isolate_posts_and_ideas(account_a, account_b):
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        run_a = Run(account_id=account_a.id, started_at=now, status="complete")
        run_b = Run(account_id=account_b.id, started_at=now, status="complete")
        session.add_all([run_a, run_b])
        session.flush()

        session.add_all(
            [
                MyPost(
                    account_id=account_a.id,
                    thread_id="post-a-1",
                    text="account a post",
                    created_at=now,
                    first_seen_run_id=run_a.id,
                ),
                MyPost(
                    account_id=account_b.id,
                    thread_id="post-b-1",
                    text="account b post",
                    created_at=now,
                    first_seen_run_id=run_b.id,
                ),
                GeneratedIdea(
                    account_id=account_a.id,
                    title="Idea A",
                    concept="for account a",
                ),
                GeneratedIdea(
                    account_id=account_b.id,
                    title="Idea B",
                    concept="for account b",
                ),
            ]
        )

    with session_scope() as session:
        posts_for_a = session.scalars(
            scope_statement(select(MyPost), MyPost, account_a.id).order_by(MyPost.thread_id)
        ).all()
        posts_for_b = session.scalars(
            scope_statement(select(MyPost), MyPost, account_b.id).order_by(MyPost.thread_id)
        ).all()
        ideas_for_a = session.scalars(
            scope_statement(select(GeneratedIdea), GeneratedIdea, account_a.id).order_by(
                GeneratedIdea.id
            )
        ).all()
        ideas_for_b = session.scalars(
            scope_statement(select(GeneratedIdea), GeneratedIdea, account_b.id).order_by(
                GeneratedIdea.id
            )
        ).all()

    assert [post.thread_id for post in posts_for_a] == ["post-a-1"]
    assert [post.thread_id for post in posts_for_b] == ["post-b-1"]
    assert [idea.title for idea in ideas_for_a] == ["Idea A"]
    assert [idea.title for idea in ideas_for_b] == ["Idea B"]


def test_cross_account_lookup_returns_empty_or_none(account_a, account_b):
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        run_a = Run(account_id=account_a.id, started_at=now, status="complete")
        session.add(run_a)
        session.flush()

        session.add_all(
            [
                MyPost(
                    account_id=account_a.id,
                    thread_id="post-a-only",
                    text="visible only to account a",
                    created_at=now,
                    first_seen_run_id=run_a.id,
                ),
                GeneratedIdea(
                    account_id=account_a.id,
                    title="Idea A Only",
                    concept="private to account a",
                ),
            ]
        )

    with session_scope() as session:
        hidden_post = get_scoped(session, MyPost, "post-a-only", account_b.id)
        hidden_idea_rows = session.scalars(
            scope_statement(select(GeneratedIdea), GeneratedIdea, account_b.id)
        ).all()

    assert hidden_post is None
    assert hidden_idea_rows == []
