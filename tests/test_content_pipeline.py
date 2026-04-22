from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from click import Group
from fastapi import APIRouter, FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from typer.main import get_command
from typer.testing import CliRunner


def _prepare_modules(monkeypatch, tmp_path):
    db_path = tmp_path / "content-pipeline.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(config)
    importlib.reload(db)

    import threads_analytics.cli as cli
    import threads_analytics.idea_generator as idea_generator
    import threads_analytics.pipeline as pipeline
    import threads_analytics.web.routes_growth as routes_growth

    importlib.reload(idea_generator)
    importlib.reload(pipeline)
    importlib.reload(cli)
    importlib.reload(routes_growth)

    db.init_db()
    return SimpleNamespace(
        cli=cli, idea_generator=idea_generator, pipeline=pipeline, routes_growth=routes_growth
    )


def test_pipeline_cli_and_route_use_same_canonical_generator(monkeypatch, tmp_path):
    modules = _prepare_modules(monkeypatch, tmp_path)
    calls: list[dict[str, object]] = []

    def fake_generate_ideas(*args, **kwargs):
        calls.append(
            {
                "topic": kwargs.get("topic"),
                "count": kwargs.get("count"),
                "has_session": kwargs.get("session") is not None,
            }
        )
        return [
            SimpleNamespace(
                id=1,
                title="Unified idea",
                concept="Specific post draft",
                predicted_score=82,
                predicted_views_range="5k-20k",
            )
        ]

    monkeypatch.setattr(modules.idea_generator, "generate_ideas", fake_generate_ideas)
    monkeypatch.setattr(
        modules.idea_generator,
        "should_generate_ideas",
        lambda session, threshold=10, account_id=None: True,
    )

    class DummyThreadsClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @classmethod
        def from_account(cls, account):
            return cls()

    monkeypatch.setattr(modules.pipeline, "ThreadsClient", DummyThreadsClient)
    monkeypatch.setattr(modules.pipeline, "ingest_own_data", lambda run, client: {"ok": True})
    monkeypatch.setattr(modules.pipeline, "extract_and_persist_topics", lambda account_id: [])
    monkeypatch.setattr(modules.pipeline, "run_lead_searches", lambda run, client: {})
    monkeypatch.setattr(modules.pipeline, "discover_affinity_creators", lambda run, client: {})
    monkeypatch.setattr(
        modules.pipeline,
        "compute_ground_truth",
        lambda session, account_id: SimpleNamespace(
            verdict_headline="ok", metrics={}, baselines={}, deltas={}
        ),
    )
    monkeypatch.setattr(
        modules.pipeline, "classify_active_experiments", lambda session, account_id: 0
    )
    monkeypatch.setattr(modules.pipeline, "auto_evaluate_due", lambda session, account_id: 0)
    monkeypatch.setattr(modules.pipeline, "generate_you_profile", lambda run: 1)
    monkeypatch.setattr(modules.pipeline, "generate_suggestions", lambda session, account_id: [])
    monkeypatch.setattr(modules.pipeline, "generate_public_perception", lambda run: 1)
    monkeypatch.setattr(modules.pipeline, "generate_algorithm_inference", lambda run: 1)
    monkeypatch.setattr(modules.pipeline, "generate_noteworthy_commentary", lambda run: [])

    router = APIRouter()
    templates = Jinja2Templates(
        directory=str(
            Path(__file__).resolve().parents[1] / "src" / "threads_analytics" / "web" / "templates"
        )
    )
    modules.routes_growth.register_growth_routes(router, templates)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    route_response = client.post(
        "/accounts/default/growth/ideas/generate",
        data={"topic": "route topic"},
        follow_redirects=False,
    )
    assert route_response.status_code == 303

    cli_result = CliRunner().invoke(
        modules.cli.app, ["generate-ideas", "cli topic", "--count", "2"]
    )
    assert cli_result.exit_code == 0

    summary = modules.pipeline.run_full_cycle()
    assert summary["accounts"][0]["ideas_generated"] == 1

    assert calls == [
        {"topic": "route topic", "count": 3, "has_session": False},
        {"topic": "cli topic", "count": 2, "has_session": False},
        {"topic": None, "count": 5, "has_session": True},
    ]


def test_generate_ideas_cli_registered_once(monkeypatch, tmp_path):
    modules = _prepare_modules(monkeypatch, tmp_path)

    command = get_command(modules.cli.app)
    assert isinstance(command, Group)
    assert "generate-ideas" in command.commands

    registered = [
        cmd for cmd in modules.cli.app.registered_commands if cmd.name == "generate-ideas"
    ]
    assert len(registered) == 1

    help_result = CliRunner().invoke(modules.cli.app, ["--help"])
    assert help_result.exit_code == 0
    assert help_result.output.count("generate-ideas") == 1


def test_planner_ranks_approved_items_with_exploit_explore(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(config)
    importlib.reload(db)
    db.init_db()

    from threads_analytics.db import session_scope
    from threads_analytics.models import GeneratedIdea
    from threads_analytics.planner import plan_account_items

    with session_scope() as session:
        idea1 = GeneratedIdea(
            account_id=1,
            title="A",
            concept="Specific hook with $500 data",
            status="approved",
            predicted_score=90,
            patterns_used=[1],
        )
        idea2 = GeneratedIdea(
            account_id=1,
            title="B",
            concept="Another specific hook with 20% growth",
            status="approved",
            predicted_score=70,
            patterns_used=[2],
        )
        session.add_all([idea1, idea2])
        session.flush()

    planned = plan_account_items(1)
    posts = [p for p in planned if p.item_type == "post"]
    assert len(posts) == 2
    # Higher predicted score should generally rank higher when no fatigue
    assert posts[0].score >= posts[1].score


def test_planner_pattern_fatigue_blocks_overuse(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(config)
    importlib.reload(db)
    db.init_db()

    from threads_analytics.db import session_scope
    from threads_analytics.models import GeneratedIdea, ContentPattern
    from threads_analytics.planner import plan_account_items, MAX_PATTERN_REUSES_14D

    with session_scope() as session:
        pattern = ContentPattern(
            account_id=1, pattern_type="hook", pattern_name="Data Hook", description="x"
        )
        session.add(pattern)
        session.flush()
        pid = pattern.id

        # Seed MAX_PATTERN_REUSES_14D uses already
        for i in range(MAX_PATTERN_REUSES_14D):
            idea = GeneratedIdea(
                account_id=1,
                title=f"Used{i}",
                concept="Used concept",
                status="published",
                patterns_used=[pid],
            )
            session.add(idea)
            session.flush()

        # New idea using same pattern should be blocked
        blocked = GeneratedIdea(
            account_id=1,
            title="Overuse",
            concept="Overused concept",
            status="approved",
            predicted_score=95,
            patterns_used=[pid],
        )
        session.add(blocked)
        session.flush()

    planned = plan_account_items(1)
    overuse = [p for p in planned if p.item_id == blocked.id]
    assert len(overuse) == 1
    assert overuse[0].score == 0.0
    assert "overused" in overuse[0].reason.lower()


def test_planner_soft_cap_blocks_posts(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(config)
    importlib.reload(db)
    db.init_db()

    from threads_analytics.db import session_scope
    from threads_analytics.models import GeneratedIdea
    from threads_analytics.planner import plan_account_items, SOFT_CAP_POSTS_PER_DAY
    from datetime import datetime, timezone

    with session_scope() as session:
        for i in range(SOFT_CAP_POSTS_PER_DAY):
            idea = GeneratedIdea(
                account_id=1,
                title=f"Cap{i}",
                concept="Cap concept",
                status="published",
                posted_at=datetime.now(timezone.utc),
            )
            session.add(idea)
            session.flush()

        over = GeneratedIdea(
            account_id=1, title="Over", concept="Over cap", status="approved", predicted_score=95
        )
        session.add(over)
        session.flush()
        over_id = over.id

    planned = plan_account_items(1)
    over_item = [p for p in planned if p.item_id == over_id]
    assert len(over_item) == 1
    assert over_item[0].score == 0.0
    assert "cap" in over_item[0].reason.lower()


def test_account_growth_score_computes_weighted_z_scores(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(config)
    importlib.reload(db)
    db.init_db()

    from datetime import datetime, timezone, timedelta
    from threads_analytics.db import session_scope
    from threads_analytics.models import MyAccountInsight, Run
    from threads_analytics.scoring import account_growth_score

    with session_scope() as session:
        run1 = Run(account_id=1, started_at=datetime.now(timezone.utc), status="complete")
        session.add(run1)
        session.flush()
        run2 = Run(account_id=1, started_at=datetime.now(timezone.utc), status="complete")
        session.add(run2)
        session.flush()

        # Older snapshot
        session.add(
            MyAccountInsight(
                account_id=1,
                run_id=run1.id,
                follower_count=1000,
                views=5000,
                likes=100,
                replies=20,
                reposts=10,
                quotes=5,
                fetched_at=datetime.now(timezone.utc) - timedelta(days=10),
            )
        )
        # Newer snapshot with growth
        session.add(
            MyAccountInsight(
                account_id=1,
                run_id=run2.id,
                follower_count=1100,
                views=7000,
                likes=150,
                replies=40,
                reposts=15,
                quotes=8,
                profile_clicks=200,
                fetched_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )

    result = account_growth_score(1)
    assert 0.0 <= result["score"] <= 100.0
    assert "follower_velocity_z" in result


def test_post_outcome_score_for_specific_post(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from threads_analytics import config, db

    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    importlib.reload(config)
    importlib.reload(db)
    db.init_db()

    from datetime import datetime, timezone, timedelta
    from threads_analytics.db import session_scope
    from threads_analytics.models import MyPost, MyPostInsight, Run
    from threads_analytics.scoring import post_outcome_score

    with session_scope() as session:
        run = Run(account_id=1, started_at=datetime.now(timezone.utc), status="complete")
        session.add(run)
        session.flush()

        post = MyPost(
            account_id=1,
            thread_id="scored_1",
            text="test",
            media_type="TEXT",
            permalink="https://threads.net/scored_1",
            created_at=datetime.now(timezone.utc),
            first_seen_run_id=run.id,
        )
        session.add(post)
        session.flush()

        session.add(
            MyPostInsight(
                account_id=1,
                thread_id="scored_1",
                run_id=run.id,
                views=1000,
                likes=100,
                replies=30,
                reposts=10,
                quotes=5,
                fetched_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        session.add(
            MyPostInsight(
                account_id=1,
                thread_id="baseline",
                run_id=run.id,
                views=500,
                likes=50,
                replies=10,
                reposts=2,
                quotes=1,
                fetched_at=datetime.now(timezone.utc) - timedelta(days=2),
            )
        )

    result = post_outcome_score(1, "scored_1")
    assert 0.0 <= result["score"] <= 100.0
    assert "views_z" in result
