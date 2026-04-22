from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from threads_analytics.brand_reporter import detect_drift, generate_weekly_report
from threads_analytics.models import Account, Base, MyPost, Run, YouProfile


def _build_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_weekly_report_is_account_scoped():
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

    session.add_all(
        [
            YouProfile(
                account_id=acc_a.id,
                run_id=run_a.id,
                core_identity="A identity",
                protect_list=[],
                stylistic_signatures=[],
            ),
            YouProfile(
                account_id=acc_b.id,
                run_id=run_b.id,
                core_identity="B identity",
                protect_list=[],
                stylistic_signatures=[],
            ),
        ]
    )
    session.commit()

    report_a = generate_weekly_report(session, account_id=acc_a.id)
    report_b = generate_weekly_report(session, account_id=acc_b.id)

    assert report_a["posts_analyzed"] == 1
    assert report_b["posts_analyzed"] == 1


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
