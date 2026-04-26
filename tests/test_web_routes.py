"""Fast web route coverage for account-prefixed routing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

pytest_plugins = ["tests.fixtures_accounts"]


def test_lifespan_skips_scheduler_when_disabled(configured_test_db, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    from threads_analytics import config
    from threads_analytics.web import app as app_mod

    config.get_settings.cache_clear()
    started = False
    stopped = False

    def fake_start_scheduler() -> None:
        nonlocal started
        started = True

    def fake_stop_scheduler() -> None:
        nonlocal stopped
        stopped = True

    monkeypatch.setattr(app_mod, "start_scheduler", fake_start_scheduler)
    monkeypatch.setattr(app_mod, "stop_scheduler", fake_stop_scheduler)

    with TestClient(app_mod.create_app()) as client:
        response = client.get("/accounts/default")

    assert response.status_code == 200
    assert started is False
    assert stopped is False


def test_lifespan_starts_scheduler_by_default(configured_test_db, monkeypatch):
    from threads_analytics import config
    from threads_analytics.web import app as app_mod

    config.get_settings.cache_clear()
    started = False
    stopped = False

    def fake_start_scheduler() -> None:
        nonlocal started
        started = True

    def fake_stop_scheduler() -> None:
        nonlocal stopped
        stopped = True

    monkeypatch.setattr(app_mod, "start_scheduler", fake_start_scheduler)
    monkeypatch.setattr(app_mod, "stop_scheduler", fake_stop_scheduler)

    with TestClient(app_mod.create_app()) as client:
        response = client.get("/accounts/default")

    assert response.status_code == 200
    assert started is True
    assert stopped is True


@pytest.fixture()
def populated_app(configured_test_db, default_account):
    from threads_analytics.db import init_db, session_scope
    from threads_analytics.models import (
        Account,
        AffinityCreator,
        AffinityPost,
        Experiment,
        GeneratedIdea,
        MyAccountInsight,
        MyPost,
        MyPostInsight,
        Profile,
        Run,
        Topic,
    )

    init_db()
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        alt_account = Account(
            slug="writer-b",
            name="Writer B",
            threads_access_token="token-b",
            threads_user_id="user-b",
            threads_handle="writer_b",
            enabled_capabilities=["analytics"],
            soft_caps={"daily_runs": 5},
        )
        session.add(alt_account)
        session.flush()

        default_run = Run(
            account_id=default_account.id,
            started_at=now,
            finished_at=now,
            status="complete",
            keyword_search_queries_used=7,
        )
        alt_run = Run(
            account_id=alt_account.id,
            started_at=now,
            finished_at=now,
            status="complete",
            keyword_search_queries_used=3,
        )
        session.add_all([default_run, alt_run])
        session.flush()

        session.add_all(
            [
                Profile(
                    account_id=default_account.id,
                    user_id="default-user",
                    username="testuser",
                    biography="test bio",
                    profile_picture_url=None,
                ),
                Profile(
                    account_id=alt_account.id,
                    user_id="alt-user",
                    username="writerb",
                    biography="alt bio",
                    profile_picture_url=None,
                ),
            ]
        )

        for i, views in enumerate((100, 200, 300)):
            post = MyPost(
                account_id=default_account.id,
                thread_id=f"default-post-{i}",
                text=f"default post {i} about building ai agents",
                media_type="TEXT",
                permalink=f"https://threads.net/default-post-{i}",
                created_at=now - timedelta(days=i + 1),
                first_seen_run_id=default_run.id,
            )
            session.add(post)
            session.flush()
            session.add(
                MyPostInsight(
                    account_id=default_account.id,
                    thread_id=post.thread_id,
                    run_id=default_run.id,
                    views=views,
                    likes=views // 5,
                    replies=i + 1,
                    reposts=0,
                    quotes=0,
                )
            )

        for i, views in enumerate((700, 800, 900)):
            post = MyPost(
                account_id=alt_account.id,
                thread_id=f"alt-post-{i}",
                text=f"writer b post {i} about remote work",
                media_type="TEXT",
                permalink=f"https://threads.net/alt-post-{i}",
                created_at=now - timedelta(days=i + 1),
                first_seen_run_id=alt_run.id,
            )
            session.add(post)
            session.flush()
            session.add(
                MyPostInsight(
                    account_id=alt_account.id,
                    thread_id=post.thread_id,
                    run_id=alt_run.id,
                    views=views,
                    likes=views // 10,
                    replies=i + 2,
                    reposts=0,
                    quotes=0,
                )
            )

        session.add_all(
            [
                MyAccountInsight(
                    account_id=default_account.id,
                    run_id=default_run.id,
                    follower_count=1234,
                    views=9999,
                    likes=500,
                    replies=80,
                    reposts=40,
                    quotes=10,
                    demographics_json={"country": {"NG": 0.6}},
                ),
                MyAccountInsight(
                    account_id=alt_account.id,
                    run_id=alt_run.id,
                    follower_count=4321,
                    views=8888,
                    likes=250,
                    replies=40,
                    reposts=10,
                    quotes=4,
                    demographics_json={"country": {"US": 0.7}},
                ),
            ]
        )

        topic = Topic(
            account_id=default_account.id,
            label="building ai agents",
            description="agents, tools, evals",
        )
        session.add(topic)
        session.flush()

        creator = AffinityCreator(
            account_id=default_account.id,
            handle="ai_builder_pro",
            user_id=None,
            discovered_via_topic_id=topic.id,
            engagement_score=650.0,
        )
        session.add(creator)
        session.flush()
        session.add(
            AffinityPost(
                account_id=default_account.id,
                thread_id="fake_aff_1",
                creator_id=creator.id,
                text="why agents need evals",
                likes=600,
                replies=120,
                reposts=60,
                quotes=20,
                created_at=now - timedelta(days=1),
            )
        )

        default_experiment = Experiment(
            account_id=default_account.id,
            title="Post between 7-9pm",
            hypothesis="Evening posts will increase reach.",
            category="TIMING",
            predicate_spec={"hours": [19, 20, 21]},
            primary_metric="reach_rate",
            status="proposed",
        )
        alt_experiment = Experiment(
            account_id=alt_account.id,
            title="Writer B images",
            hypothesis="Images improve profile clicks.",
            category="MEDIA",
            predicate_spec={"media_types": ["IMAGE"]},
            primary_metric="profile_click_rate",
            status="active",
        )
        session.add_all([default_experiment, alt_experiment])
        session.flush()
        default_experiment_id = default_experiment.id

        default_idea = GeneratedIdea(
            account_id=default_account.id,
            title="Default draft idea",
            concept="Ship the eval loop before scaling the agent.",
            predicted_score=82,
            predicted_views_range="1k-5k",
            status="draft",
        )
        alt_idea = GeneratedIdea(
            account_id=alt_account.id,
            title="Writer B scheduled idea",
            concept="Remote teams need stronger writing rituals.",
            predicted_score=91,
            predicted_views_range="5k-20k",
            status="scheduled",
            scheduled_at=now + timedelta(days=1),
        )
        session.add_all([default_idea, alt_idea])
        session.flush()
        default_idea_id = default_idea.id

    from threads_analytics.web.app import create_app

    app = create_app()
    return TestClient(app), {
        "default": default_account.slug,
        "alt": "writer-b",
        "default_idea_id": default_idea_id,
        "default_experiment_id": default_experiment_id,
    }


def test_ground_truth_prefixed_route_renders(populated_app):
    client, ids = populated_app
    r = client.get(f"/accounts/{ids['default']}")
    assert r.status_code == 200
    assert "testuser" in r.text
    assert "active" in r.text


def test_legacy_ground_truth_redirects_to_prefixed(populated_app):
    client, ids = populated_app
    r = client.get(f"/?account={ids['default']}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/accounts/{ids['default']}"


def test_prefixed_content_and_calendar_routes_render(populated_app):
    client, ids = populated_app
    content = client.get(f"/accounts/{ids['default']}/content")
    assert content.status_code == 200
    assert "Default draft idea" in content.text

    calendar = client.get(f"/accounts/{ids['alt']}/calendar")
    assert calendar.status_code == 200
    assert "Content Calendar" in calendar.text
    assert "Remote teams need stronger writing rituals." in calendar.text


def test_legacy_read_routes_redirect_to_prefixed(populated_app):
    client, ids = populated_app
    content = client.get(f"/content?account={ids['default']}", follow_redirects=False)
    assert content.status_code == 303
    assert content.headers["location"] == f"/accounts/{ids['default']}/content"

    calendar = client.get(
        f"/calendar?account={ids['default']}&week=2",
        follow_redirects=False,
    )
    assert calendar.status_code == 303
    assert calendar.headers["location"] == f"/accounts/{ids['default']}/calendar?week=2"

    experiments = client.get(
        f"/experiments?account={ids['default']}",
        follow_redirects=False,
    )
    assert experiments.status_code == 303
    assert experiments.headers["location"] == f"/accounts/{ids['default']}/experiments"


def test_prefixed_experiments_routes_render(populated_app):
    client, ids = populated_app
    index = client.get(f"/accounts/{ids['default']}/experiments")
    assert index.status_code == 200
    assert "Post between 7-9pm" in index.text

    detail = client.get(f"/accounts/{ids['default']}/experiments/{ids['default_experiment_id']}")
    assert detail.status_code == 200
    assert "Evening posts will increase reach." in detail.text


def test_growth_performance_is_account_scoped(populated_app):
    client, ids = populated_app
    default_perf = client.get(f"/accounts/{ids['default']}/growth/performance")
    alt_perf = client.get(f"/accounts/{ids['alt']}/growth/performance")

    assert default_perf.status_code == 200
    assert alt_perf.status_code == 200
    assert "200" in default_perf.text
    assert "800" in alt_perf.text
    assert "800" not in default_perf.text


def test_growth_ideas_prefixed_route_filters_by_account(populated_app):
    client, ids = populated_app
    default_ideas = client.get(f"/accounts/{ids['default']}/growth/ideas")
    alt_ideas = client.get(f"/accounts/{ids['alt']}/growth/ideas?status=scheduled")

    assert default_ideas.status_code == 200
    assert alt_ideas.status_code == 200
    assert "Default draft idea" in default_ideas.text
    assert "Writer B scheduled idea" not in default_ideas.text
    assert "Writer B scheduled idea" in alt_ideas.text


def test_legacy_mutating_route_is_rejected(populated_app):
    client, ids = populated_app
    r = client.post(f"/api/content/{ids['default_idea_id']}/dismiss")
    assert r.status_code == 400
    assert r.json()["error"] == "Use account-prefixed route"


def test_prefixed_run_status_route(populated_app):
    client, ids = populated_app
    r = client.get(f"/accounts/{ids['default']}/run/status")
    assert r.status_code == 200
    data = r.json()
    assert "running" in data
    assert "last_summary" in data


def test_comments_run_status_includes_mission_control_progress(
    populated_app, default_account, monkeypatch
):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import Run
    from threads_analytics.web import routes_common, routes_pipeline

    routes_common._last_comments_run_summaries = {}

    class _ImmediateThread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    def fake_run_comments_cycle(*, draft_max, min_tier, account_slug):
        assert draft_max == 15
        assert min_tier == "medium"
        assert account_slug == ids["default"]

        with session_scope() as session:
            run = Run(
                account_id=default_account.id,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                status="complete",
                stage_progress={
                    "comment_inbox_sync": {"status": "complete", "at": "2026-04-17T00:00:00+00:00"},
                    "comment_drafts": {"status": "complete", "at": "2026-04-17T00:01:00+00:00"},
                    "leads_search": {"status": "complete", "at": "2026-04-17T00:02:00+00:00"},
                },
            )
            session.add(run)
            session.flush()
            run_id = run.id

        return {
            "run_id": run_id,
            "account": account_slug,
            "comment_inbox_sync": {"inserted": 2, "updated": 1},
            "comment_drafts": {"drafted": 3},
        }

    monkeypatch.setattr(routes_pipeline, "run_comments_cycle", fake_run_comments_cycle)
    monkeypatch.setattr(routes_pipeline.threading, "Thread", _ImmediateThread)

    post = client.post(f"/accounts/{ids['default']}/run/comments")
    assert post.status_code == 200
    assert post.json() == {"status": "started"}

    status = client.get(f"/accounts/{ids['default']}/run/comments/status")
    assert status.status_code == 200
    data = status.json()
    assert data["last_comments_run_summary"]["comment_inbox_sync"] == {"inserted": 2, "updated": 1}
    assert data["last_comments_run_summary"]["comment_drafts"] == {"drafted": 3}
    assert data["stage_progress"]["comment_inbox_sync"]["status"] == "complete"
    assert data["stage_progress"]["comment_drafts"]["status"] == "complete"


def test_legacy_comments_run_mutation_rejected(populated_app):
    client, _ = populated_app
    response = client.post("/run/comments")
    assert response.status_code == 400
    assert response.json()["error"] == "Use account-prefixed route"


def test_recommendations_and_learning_redirect_to_prefixed_pages(populated_app):
    client, ids = populated_app
    rec = client.get(f"/recommendations?account={ids['default']}", follow_redirects=False)
    assert rec.status_code == 303
    assert rec.headers["location"] == f"/accounts/{ids['default']}/recommendations"

    learning = client.get(f"/learning?account={ids['default']}", follow_redirects=False)
    assert learning.status_code == 303
    assert learning.headers["location"] == f"/accounts/{ids['default']}/experiments"


def test_notifications_render_on_prefixed_homepage(populated_app, default_account):
    client, ids = populated_app
    from threads_analytics.db import session_scope
    from threads_analytics.models import Notification

    with session_scope() as session:
        session.add(
            Notification(
                account_id=default_account.id,
                alert_type="token_expiry",
                title="Token expiring soon",
                message="Refresh your access token",
                link_path="/settings",
            )
        )

    r = client.get(f"/accounts/{ids['default']}")
    assert r.status_code == 200
    assert "Token expiring soon" in r.text


def test_webhook_and_notification_endpoints_still_work(populated_app, default_account):
    client, ids = populated_app
    from threads_analytics.db import session_scope
    from threads_analytics.models import Account

    with session_scope() as session:
        account = session.get(Account, default_account.id)
        assert account is not None
        account.threads_user_id = "default-user"

    payload = {
        "object": "threads",
        "entry": [{"changes": [{"value": {"thread_id": "123"}}]}],
    }
    webhook = client.post("/webhook/threads", json=payload)
    assert webhook.status_code == 400

    matched_payload = {
        "object": "threads",
        "entry": [{"changes": [{"value": {"user_id": "default-user"}}]}],
    }
    matched_webhook = client.post("/webhook/threads", json=matched_payload)
    assert matched_webhook.status_code == 200
    assert matched_webhook.json()["success"] is True

    notes = client.get("/accounts/default/api/notifications")
    assert notes.status_code == 200
    assert any(n["alert_type"] == "webhook_event" for n in notes.json()["notifications"])

    from threads_analytics.db import session_scope
    from threads_analytics.models import Notification

    with session_scope() as session:
        note = Notification(
            account_id=default_account.id,
            alert_type="quota_exhausted",
            title="Quota reached",
            message="Daily quota exhausted",
        )
        session.add(note)
        session.flush()
        note_id = note.id

    dismiss = client.post(f"/accounts/default/api/notifications/{note_id}/dismiss")
    assert dismiss.status_code == 200
    assert dismiss.json()["success"] is True


def test_portfolio_route_remains_prefix_free(populated_app):
    client, _ = populated_app
    r = client.get("/portfolio")
    assert r.status_code == 200
    assert "Aggregate Growth Score" in r.text
    assert "Top Patterns" in r.text
    assert "Action Queue" in r.text


def test_static_css_served(populated_app):
    client, _ = populated_app
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "--bg" in r.text


def test_comment_mission_control_redirect(populated_app):
    client, ids = populated_app
    response = client.get(f"/comments?account={ids['default']}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/accounts/{ids['default']}/comments"


def test_comment_mission_control_account_scope(populated_app, default_account):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import Account, CommentInbox, Run

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        alt_account = session.query(Account).filter_by(slug=ids["alt"]).one()
        default_run = session.query(Run).filter_by(account_id=default_account.id).first()
        alt_run = session.query(Run).filter_by(account_id=alt_account.id).first()
        assert default_run is not None
        assert alt_run is not None

        session.add_all(
            [
                CommentInbox(
                    account_id=default_account.id,
                    source_post_thread_id="default-source-post",
                    source_post_text="default source post",
                    source_post_created_at=now,
                    comment_thread_id="default-comment-thread",
                    comment_permalink="https://threads.net/default-comment-thread",
                    comment_author_username="default-follower",
                    comment_author_user_id="default-follower-id",
                    comment_text="default account comment",
                    comment_created_at=now,
                    first_seen_run_id=default_run.id,
                    last_seen_at=now,
                ),
                CommentInbox(
                    account_id=alt_account.id,
                    source_post_thread_id="alt-source-post",
                    source_post_text="alt source post",
                    source_post_created_at=now,
                    comment_thread_id="alt-comment-thread",
                    comment_permalink="https://threads.net/alt-comment-thread",
                    comment_author_username="alt-follower",
                    comment_author_user_id="alt-follower-id",
                    comment_text="alt account comment",
                    comment_created_at=now,
                    first_seen_run_id=alt_run.id,
                    last_seen_at=now,
                ),
            ]
        )

    default_response = client.get(f"/accounts/{ids['default']}/comments")
    assert default_response.status_code == 200
    assert "default account comment" in default_response.text
    assert "alt account comment" not in default_response.text

    alt_response = client.get(f"/accounts/{ids['alt']}/comments")
    assert alt_response.status_code == 200
    assert "alt account comment" in alt_response.text
    assert "default account comment" not in alt_response.text


def test_comment_bulk_action_rejects_cross_account_ids(populated_app, default_account):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import Account, CommentInbox, Run

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        alt_account = session.query(Account).filter_by(slug=ids["alt"]).one()
        alt_run = session.query(Run).filter_by(account_id=alt_account.id).first()
        assert alt_run is not None

        alt_item = CommentInbox(
            account_id=alt_account.id,
            source_post_thread_id="alt-cross-source-post",
            source_post_text="alt source post",
            source_post_created_at=now,
            comment_thread_id="alt-cross-comment-thread",
            comment_permalink="https://threads.net/alt-cross-comment-thread",
            comment_author_username="alt-follower",
            comment_author_user_id="alt-follower-id",
            comment_text="alt cross account comment",
            comment_created_at=now,
            first_seen_run_id=alt_run.id,
            last_seen_at=now,
            status=CommentInbox.STATUS_APPROVED,
            ai_draft_reply="ready to send",
        )
        session.add(alt_item)
        session.flush()
        alt_item_id = alt_item.id

    send_response = client.post(
        f"/accounts/{ids['default']}/comments/api/send",
        json={"ids": [alt_item_id]},
    )
    assert send_response.status_code == 404
    assert send_response.json()["error"] == "One or more items not found"

    edit_response = client.post(
        f"/accounts/{ids['default']}/comments/api/edit",
        json={"id": alt_item_id, "text": "new reply"},
    )
    assert edit_response.status_code == 404
    assert edit_response.json()["error"] == "Not found"


def test_legacy_comment_mutation_rejected(populated_app):
    client, _ = populated_app
    response = client.post("/comments/api/approve")
    assert response.status_code == 400
    assert response.json()["error"] == "Use account-prefixed route"


# ---------------------------------------------------------------------------
# Task 10 — Route-level integration tests for Mission Control workflows
# ---------------------------------------------------------------------------


class _RecordingReplyClient:
    """Stub ThreadsClient that records create_reply calls."""

    def __init__(self, *, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls: list[tuple[str, str]] = []

    def create_reply(self, reply_to_id: str, text: str) -> dict[str, str]:
        self.calls.append((reply_to_id, text))
        if self.should_fail:
            raise RuntimeError("Threads publish exploded")
        return {"id": "published-123", "creation_id": "creation-123"}


def _seed_comment_inbox_item(
    session,
    *,
    account_id: int,
    run_id: int,
    comment_thread_id: str,
    status: str = "drafted",
    ai_draft_reply: str | None = "AI draft",
    final_reply: str | None = None,
) -> int:
    """Insert a CommentInbox row and return its id."""
    from threads_analytics.models import CommentInbox

    now = datetime.now(timezone.utc)
    item = CommentInbox(
        account_id=account_id,
        source_post_thread_id=f"src-{comment_thread_id}",
        source_post_text=f"Source for {comment_thread_id}",
        source_post_created_at=now - timedelta(days=1),
        comment_thread_id=comment_thread_id,
        comment_permalink=f"https://threads.net/{comment_thread_id}",
        comment_author_username="testuser",
        comment_author_user_id="testuser-id",
        comment_text="Great insight!",
        comment_created_at=now,
        status=status,
        ai_draft_reply=ai_draft_reply,
        final_reply=final_reply,
        approved_at=now if status == CommentInbox.STATUS_APPROVED else None,
        first_seen_run_id=run_id,
        last_seen_at=now,
    )
    session.add(item)
    session.flush()
    return item.id


def test_comment_send_happy_path(populated_app, default_account, monkeypatch):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import CommentInbox, Run
    from threads_analytics.publish_gate import GateResult

    with session_scope() as session:
        run = session.query(Run).filter_by(account_id=default_account.id).first()
        assert run is not None
        item_id = _seed_comment_inbox_item(
            session,
            account_id=default_account.id,
            run_id=run.id,
            comment_thread_id="send-happy",
            status=CommentInbox.STATUS_APPROVED,
            ai_draft_reply="Thanks!",
            final_reply="Thanks for reading!",
        )

    mock_client = _RecordingReplyClient()

    monkeypatch.setattr(
        "threads_analytics.comment_inbox.gate_send_comment",
        lambda _inbox_id: GateResult(allowed=True),
    )
    monkeypatch.setattr(
        "threads_analytics.threads_client.ThreadsClient.from_account",
        lambda _account: mock_client,
    )

    r = client.post(
        f"/accounts/{ids['default']}/comments/api/send",
        json={"ids": [item_id]},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True, "sent": 1, "failed": 0, "skipped": 0}

    with session_scope() as session:
        refreshed = session.get(CommentInbox, item_id)
        assert refreshed is not None
        assert refreshed.status == CommentInbox.STATUS_SENT
        assert refreshed.sent_at is not None
        assert refreshed.published_reply_thread_id == "published-123"
        assert len(mock_client.calls) == 1
        assert mock_client.calls[0] == ("send-happy", "Thanks for reading!")


def test_comment_edit_demotes_approved_to_drafted(populated_app, default_account):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import CommentInbox, Run

    with session_scope() as session:
        run = session.query(Run).filter_by(account_id=default_account.id).first()
        assert run is not None
        item_id = _seed_comment_inbox_item(
            session,
            account_id=default_account.id,
            run_id=run.id,
            comment_thread_id="edit-demote",
            status=CommentInbox.STATUS_APPROVED,
            final_reply="old reply",
        )

    r = client.post(
        f"/accounts/{ids['default']}/comments/api/edit",
        json={"id": item_id, "text": "Updated reply"},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}

    with session_scope() as session:
        refreshed = session.get(CommentInbox, item_id)
        assert refreshed is not None
        assert refreshed.status == CommentInbox.STATUS_DRAFTED
        assert refreshed.final_reply == "Updated reply"
        assert refreshed.approved_at is None


def test_comment_bulk_approve_then_send(populated_app, default_account, monkeypatch):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import CommentInbox, Run
    from threads_analytics.publish_gate import GateResult

    with session_scope() as session:
        run = session.query(Run).filter_by(account_id=default_account.id).first()
        assert run is not None
        id_a = _seed_comment_inbox_item(
            session,
            account_id=default_account.id,
            run_id=run.id,
            comment_thread_id="bulk-a",
            status=CommentInbox.STATUS_DRAFTED,
            ai_draft_reply="Draft A",
        )
        id_b = _seed_comment_inbox_item(
            session,
            account_id=default_account.id,
            run_id=run.id,
            comment_thread_id="bulk-b",
            status=CommentInbox.STATUS_DRAFTED,
            ai_draft_reply="Draft B",
        )

    approve_r = client.post(
        f"/accounts/{ids['default']}/comments/api/approve",
        json={"ids": [id_a, id_b]},
    )
    assert approve_r.status_code == 200
    assert approve_r.json()["approved"] == 2

    with session_scope() as session:
        for item_id in (id_a, id_b):
            item = session.get(CommentInbox, item_id)
            assert item is not None
            assert item.status == CommentInbox.STATUS_APPROVED

    mock_client = _RecordingReplyClient()
    monkeypatch.setattr(
        "threads_analytics.comment_inbox.gate_send_comment",
        lambda _inbox_id: GateResult(allowed=True),
    )
    monkeypatch.setattr(
        "threads_analytics.threads_client.ThreadsClient.from_account",
        lambda _account: mock_client,
    )

    send_r = client.post(
        f"/accounts/{ids['default']}/comments/api/send",
        json={"ids": [id_a, id_b]},
    )
    assert send_r.status_code == 200
    assert send_r.json() == {"success": True, "sent": 2, "failed": 0, "skipped": 0}

    with session_scope() as session:
        for item_id in (id_a, id_b):
            item = session.get(CommentInbox, item_id)
            assert item is not None
            assert item.status == CommentInbox.STATUS_SENT
            assert item.sent_at is not None


def test_comment_send_failure_surfaces_in_api(populated_app, default_account, monkeypatch):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import CommentInbox, Run
    from threads_analytics.publish_gate import GateResult

    with session_scope() as session:
        run = session.query(Run).filter_by(account_id=default_account.id).first()
        assert run is not None
        item_id = _seed_comment_inbox_item(
            session,
            account_id=default_account.id,
            run_id=run.id,
            comment_thread_id="send-fail",
            status=CommentInbox.STATUS_APPROVED,
            final_reply="Reply that will fail",
        )

    mock_client = _RecordingReplyClient(should_fail=True)
    monkeypatch.setattr(
        "threads_analytics.comment_inbox.gate_send_comment",
        lambda _inbox_id: GateResult(allowed=True),
    )
    monkeypatch.setattr(
        "threads_analytics.threads_client.ThreadsClient.from_account",
        lambda _account: mock_client,
    )

    r = client.post(
        f"/accounts/{ids['default']}/comments/api/send",
        json={"ids": [item_id]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["failed"] == 1
    assert data["sent"] == 0

    with session_scope() as session:
        refreshed = session.get(CommentInbox, item_id)
        assert refreshed is not None
        assert refreshed.status == CommentInbox.STATUS_SEND_FAILED
        assert refreshed.send_error is not None
        assert len(refreshed.send_error) > 0


class _RecordingLLMClient:
    def __init__(self, text: str):
        self._text = text

    def create_message(self, **kwargs):
        class _Response:
            pass

        resp = _Response()
        resp.text = self._text
        return resp


def test_comment_regenerate_selected(populated_app, default_account, monkeypatch):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import CommentInbox, Run

    with session_scope() as session:
        run = session.query(Run).filter_by(account_id=default_account.id).first()
        assert run is not None
        item_no_draft = CommentInbox(
            account_id=default_account.id,
            source_post_thread_id="src-regen-1",
            source_post_text="Source 1",
            source_post_created_at=datetime.now(timezone.utc) - timedelta(days=1),
            comment_thread_id="regen-1",
            comment_permalink="https://threads.net/regen-1",
            comment_author_username="reader",
            comment_author_user_id="reader-1",
            comment_text="First",
            comment_created_at=datetime.now(timezone.utc),
            status=CommentInbox.STATUS_DRAFTED,
            ai_draft_reply=None,
            final_reply=None,
            first_seen_run_id=run.id,
            last_seen_at=datetime.now(timezone.utc),
        )
        item_old_draft = CommentInbox(
            account_id=default_account.id,
            source_post_thread_id="src-regen-2",
            source_post_text="Source 2",
            source_post_created_at=datetime.now(timezone.utc) - timedelta(days=1),
            comment_thread_id="regen-2",
            comment_permalink="https://threads.net/regen-2",
            comment_author_username="reader",
            comment_author_user_id="reader-2",
            comment_text="Second",
            comment_created_at=datetime.now(timezone.utc),
            status=CommentInbox.STATUS_DRAFTED,
            ai_draft_reply="old",
            final_reply=None,
            first_seen_run_id=run.id,
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add_all([item_no_draft, item_old_draft])
        session.flush()
        id_no_draft = item_no_draft.id
        id_old_draft = item_old_draft.id

    monkeypatch.setattr(
        "threads_analytics.comment_reply_drafts.get_llm_client",
        lambda: _RecordingLLMClient("Fresh regenerated draft"),
    )

    r = client.post(
        f"/accounts/{ids['default']}/comments/api/regenerate",
        json={"ids": [id_no_draft, id_old_draft]},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True, "drafted": 2}

    with session_scope() as session:
        refreshed_no = session.get(CommentInbox, id_no_draft)
        refreshed_old = session.get(CommentInbox, id_old_draft)
        assert refreshed_no is not None
        assert refreshed_old is not None
        assert refreshed_no.ai_draft_reply == "Fresh regenerated draft"
        assert refreshed_old.ai_draft_reply == "Fresh regenerated draft"


def test_comment_regenerate_rejects_cross_account(populated_app, default_account):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import Account, CommentInbox, Run

    with session_scope() as session:
        alt_account = session.query(Account).filter_by(slug=ids["alt"]).one()
        alt_run = session.query(Run).filter_by(account_id=alt_account.id).first()
        assert alt_run is not None

        alt_item = CommentInbox(
            account_id=alt_account.id,
            source_post_thread_id="alt-regen",
            source_post_text="Alt source",
            source_post_created_at=datetime.now(timezone.utc) - timedelta(days=1),
            comment_thread_id="alt-regen",
            comment_permalink="https://threads.net/alt-regen",
            comment_author_username="alt",
            comment_author_user_id="alt-id",
            comment_text="Alt comment",
            comment_created_at=datetime.now(timezone.utc),
            status=CommentInbox.STATUS_DRAFTED,
            ai_draft_reply=None,
            final_reply=None,
            first_seen_run_id=alt_run.id,
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(alt_item)
        session.flush()
        alt_item_id = alt_item.id

    r = client.post(
        f"/accounts/{ids['default']}/comments/api/regenerate",
        json={"ids": [alt_item_id]},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "One or more items not found"


def test_brand_check_is_account_scoped(populated_app, default_account, monkeypatch):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import Account, Run, YouProfile

    with session_scope() as session:
        alt_account = session.query(Account).filter_by(slug=ids["alt"]).one()
        default_run = session.query(Run).filter_by(account_id=default_account.id).first()
        alt_run = session.query(Run).filter_by(account_id=alt_account.id).first()
        assert default_run is not None
        assert alt_run is not None

        session.add(
            YouProfile(
                account_id=default_account.id,
                run_id=default_run.id,
                core_identity="Default voice",
                protect_list=["badword"],
                double_down_list=["signature"],
                stylistic_signatures=[],
            )
        )
        session.add(
            YouProfile(
                account_id=alt_account.id,
                run_id=alt_run.id,
                core_identity="Alt voice",
                protect_list=[],
                double_down_list=[],
                stylistic_signatures=[],
            )
        )

    class _FakeValidator:
        def __init__(self, profile):
            self.profile = profile

    def fake_validate(text, you_profile):
        return type(
            "Result",
            (),
            {
                "overall_score": 85 if you_profile.core_identity == "Default voice" else 60,
                "passed": True,
                "protect_violations": ["badword"]
                if you_profile.core_identity == "Default voice"
                else [],
                "double_down_elements": ["signature"]
                if you_profile.core_identity == "Default voice"
                else [],
                "suggestions": [],
            },
        )()

    monkeypatch.setattr("threads_analytics.web.routes_brand.validate_content", fake_validate)

    default_r = client.post(
        f"/accounts/{ids['default']}/api/brand-check",
        data={"text": "hello badword"},
    )
    assert default_r.status_code == 200
    default_data = default_r.json()
    assert default_data["score"] == 85
    assert default_data["violations"] == ["badword"]
    assert default_data["double_down_elements"] == ["signature"]

    alt_r = client.post(
        f"/accounts/{ids['alt']}/api/brand-check",
        data={"text": "hello world"},
    )
    assert alt_r.status_code == 200
    alt_data = alt_r.json()
    assert alt_data["score"] == 60
    assert alt_data["violations"] == []
    assert alt_data["double_down_elements"] == []


def test_brand_compose_prefixed_route_renders(populated_app):
    client, ids = populated_app
    r = client.get(f"/accounts/{ids['default']}/compose")
    assert r.status_code == 200
    assert "Brand Composer" in r.text or "composer" in r.text.lower()


def test_brand_report_prefixed_route_renders(populated_app):
    client, ids = populated_app
    r = client.get(f"/accounts/{ids['default']}/brand-report")
    assert r.status_code == 200


def test_brand_legacy_routes_redirect(populated_app):
    client, ids = populated_app
    compose = client.get("/compose", follow_redirects=False)
    assert compose.status_code == 303
    assert compose.headers["location"] == f"/accounts/{ids['default']}/compose"

    report = client.get("/brand-report", follow_redirects=False)
    assert report.status_code == 303
    assert report.headers["location"] == f"/accounts/{ids['default']}/brand-report"


def test_growth_patterns_prefixed_route_renders(populated_app, default_account):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import ContentPattern, Run

    with session_scope() as session:
        run = session.query(Run).filter_by(account_id=default_account.id).first()
        assert run is not None
        session.add(
            ContentPattern(
                account_id=default_account.id,
                pattern_type="hook",
                pattern_name="Specific claim hook",
                description="Starts with a specific claim.",
                confidence_score=0.85,
                example_count=5,
                avg_views=3200.0,
                success_rate=0.8,
                example_post_ids=[],
                is_active=True,
            )
        )

    r = client.get(f"/accounts/{ids['default']}/growth/patterns")
    assert r.status_code == 200
    assert "Specific claim hook" in r.text
    assert "Your Winning Patterns" in r.text


def test_comment_sync_drafts_all_pending_without_artificial_cap(
    populated_app, default_account, monkeypatch
):
    client, ids = populated_app

    from threads_analytics.db import session_scope
    from threads_analytics.models import CommentInbox, MyPost, Run

    with session_scope() as session:
        run = session.query(Run).filter_by(account_id=default_account.id).first()
        assert run is not None

        old_post = MyPost(
            account_id=default_account.id,
            thread_id="old-post-1",
            text="old post",
            media_type="TEXT",
            permalink="https://threads.net/old-post-1",
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
            first_seen_run_id=run.id,
        )
        session.add(old_post)
        session.flush()

        for i in range(20):
            session.add(
                CommentInbox(
                    account_id=default_account.id,
                    source_post_thread_id=old_post.thread_id,
                    source_post_text=old_post.text,
                    source_post_created_at=old_post.created_at,
                    comment_thread_id=f"bulk-comment-{i}",
                    comment_permalink=f"https://threads.net/bulk-comment-{i}",
                    comment_author_username="reader",
                    comment_author_user_id=f"reader-{i}",
                    comment_text=f"Comment {i}",
                    comment_created_at=datetime.now(timezone.utc) - timedelta(hours=i),
                    status=CommentInbox.STATUS_DRAFTED,
                    ai_draft_reply=None,
                    final_reply=None,
                    first_seen_run_id=run.id,
                    last_seen_at=datetime.now(timezone.utc),
                )
            )

    class _FakeClient:
        def list_my_posts(self, limit: int = 100):
            return []

        def list_post_replies(self, post_thread_id: str, limit: int = 25):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(
        "threads_analytics.threads_client.ThreadsClient.from_account",
        lambda _account: _FakeClient(),
    )
    monkeypatch.setattr(
        "threads_analytics.comment_reply_drafts.get_llm_client",
        lambda: _RecordingLLMClient("Auto draft"),
    )

    from threads_analytics.pipeline import _run_comments_cycle_for_account
    from threads_analytics.models import Account

    with session_scope() as session:
        account = session.get(Account, default_account.id)
        summary = _run_comments_cycle_for_account(account)

    assert summary.get("comment_drafts", 0) == 20

    with session_scope() as session:
        drafted_count = (
            session.query(CommentInbox)
            .filter(
                CommentInbox.account_id == default_account.id,
                CommentInbox.ai_draft_reply.isnot(None),
            )
            .count()
        )
        assert drafted_count == 20
