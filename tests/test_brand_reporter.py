from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from threads_analytics import brand_reporter
from threads_analytics.brand_reporter import detect_drift, generate_weekly_report
from threads_analytics.models import Account, Base, MyPost, Run, YouProfile


MONDAY_NOON = datetime(2026, 4, 27, 12, tzinfo=timezone.utc)


def _build_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_account_with_run(session: Session, slug: str, name: str, now: datetime) -> tuple[Account, Run]:
    account = Account(slug=slug, name=name)
    session.add(account)
    session.flush()

    run = Run(account_id=account.id, started_at=now, status="complete")
    session.add(run)
    session.flush()
    return account, run


def _add_profile(session: Session, account: Account, run: Run, identity: str) -> YouProfile:
    profile = YouProfile(
        account_id=account.id,
        run_id=run.id,
        core_identity=identity,
        protect_list=[],
        stylistic_signatures=[],
    )
    session.add(profile)
    return profile


class _MondayDate(date):
    @classmethod
    def today(cls) -> date:
        return date(2026, 4, 27)


def test_weekly_report_is_account_scoped(monkeypatch):
    monkeypatch.setattr(brand_reporter, "date", _MondayDate)
    session = _build_session()
    now = MONDAY_NOON

    acc_a, run_a = _add_account_with_run(session, "a", "A", now)
    acc_b, run_b = _add_account_with_run(session, "b", "B", now)

    session.add_all(
        [
            MyPost(
                account_id=acc_a.id,
                thread_id="a-post",
                text="A post text",
                created_at=now - timedelta(days=1),
                first_seen_run_id=run_a.id,
            ),
            MyPost(
                account_id=acc_b.id,
                thread_id="b-post",
                text="B post text",
                created_at=now - timedelta(days=1),
                first_seen_run_id=run_b.id,
            ),
        ]
    )

    _add_profile(session, acc_a, run_a, "A identity")
    _add_profile(session, acc_b, run_b, "B identity")
    session.commit()

    report_a = generate_weekly_report(session, account_id=acc_a.id)
    report_b = generate_weekly_report(session, account_id=acc_b.id)

    assert report_a["posts_analyzed"] == 1
    assert report_b["posts_analyzed"] == 1


def test_weekly_report_includes_previous_day_on_monday(monkeypatch):
    monkeypatch.setattr(brand_reporter, "date", _MondayDate)
    session = _build_session()
    now = MONDAY_NOON
    account, run = _add_account_with_run(session, "a", "A", now)

    session.add(
        MyPost(
            account_id=account.id,
            thread_id="sunday-post",
            text="This Sunday post should still appear in Monday's weekly report.",
            created_at=datetime(2026, 4, 26, 10, tzinfo=timezone.utc),
            first_seen_run_id=run.id,
        )
    )
    _add_profile(session, account, run, "A identity")
    session.commit()

    report = generate_weekly_report(session, account_id=account.id)

    assert report["week_start"] == date(2026, 4, 21)
    assert report["week_end"] == date(2026, 4, 27)
    assert report["posts_analyzed"] == 1


def test_weekly_average_scores_use_adjacent_rolling_windows(monkeypatch):
    monkeypatch.setattr(brand_reporter, "date", _MondayDate)
    session = _build_session()
    now = MONDAY_NOON
    account, run = _add_account_with_run(session, "a", "A", now)
    profile = _add_profile(session, account, run, "A identity")
    session.flush()

    session.add_all(
        [
            MyPost(
                account_id=account.id,
                thread_id="current-sunday",
                text="Current rolling week post with enough words to pass neutral checks.",
                created_at=datetime(2026, 4, 26, 10, tzinfo=timezone.utc),
                first_seen_run_id=run.id,
            ),
            MyPost(
                account_id=account.id,
                thread_id="previous-sunday",
                text="Previous rolling week post with enough words to pass neutral checks.",
                created_at=datetime(2026, 4, 19, 10, tzinfo=timezone.utc),
                first_seen_run_id=run.id,
            ),
        ]
    )
    session.commit()

    scores = brand_reporter._get_weekly_average_scores(
        session, profile, weeks=2, account_id=account.id
    )

    assert scores[0]["week_start"] == date(2026, 4, 21)
    assert scores[0]["week_end"] == date(2026, 4, 27)
    assert scores[0]["post_count"] == 1
    assert scores[1]["week_start"] == date(2026, 4, 14)
    assert scores[1]["week_end"] == date(2026, 4, 20)
    assert scores[1]["post_count"] == 1


def test_detect_drift_is_account_scoped():
    session = _build_session()
    now = datetime.now(timezone.utc)

    acc_a = Account(slug="a", name="A")
    acc_b = Account(slug="b", name="B")
    session.add_all([acc_a, acc_b])
    session.flush()

    run_a = Run(account_id=acc_a.id, started_at=now, status="complete")
    run_b = Run(account_id=acc_b.id, started_at=now, status="complete")
    session.add_all([run_a, run_b])
    session.flush()

    for i in range(5):
        session.add(
            MyPost(
                account_id=acc_a.id,
                thread_id=f"a-post-{i}",
                text="A voice signature phrase here",
                created_at=now - timedelta(days=i),
                first_seen_run_id=run_a.id,
            )
        )

    session.add(
        MyPost(
            account_id=acc_b.id,
            thread_id="b-post-0",
            text="B post text",
            created_at=now - timedelta(days=1),
            first_seen_run_id=run_b.id,
        )
    )

    session.add(
        YouProfile(
            account_id=acc_a.id,
            run_id=run_a.id,
            core_identity="A identity",
            protect_list=["voice signature phrase"],
            stylistic_signatures=[],
        )
    )
    session.commit()

    alerts_a = detect_drift(session, account_id=acc_a.id)
    alerts_b = detect_drift(session, account_id=acc_b.id)

    alert_types = {a["alert_type"] for a in alerts_a}
    assert "protect_violation" in alert_types
    assert len(alerts_b) == 0
