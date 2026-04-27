from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest_plugins = ["tests.fixtures_accounts"]


def _add_account_snapshot(session, account_id: int, run_id: int, followers: int, fetched_at: datetime):
    from threads_analytics.models import MyAccountInsight

    session.add(
        MyAccountInsight(
            account_id=account_id,
            run_id=run_id,
            follower_count=followers,
            fetched_at=fetched_at,
        )
    )


def test_follower_velocity_uses_available_snapshot_history(configured_test_db, default_account):
    from threads_analytics.db import session_scope
    from threads_analytics.metrics import METRIC_FOLLOWER_VELOCITY, compute_metric
    from threads_analytics.models import Run

    now = datetime(2026, 4, 27, 8, 0, tzinfo=timezone.utc)
    start = now - timedelta(days=4)

    with session_scope() as session:
        first = Run(account_id=default_account.id, started_at=start, status="complete")
        latest = Run(account_id=default_account.id, started_at=now, status="complete")
        session.add_all([first, latest])
        session.flush()
        _add_account_snapshot(session, default_account.id, first.id, 369, start)
        _add_account_snapshot(session, default_account.id, latest.id, 496, now)

        metric = compute_metric(
            session,
            METRIC_FOLLOWER_VELOCITY,
            now - timedelta(days=14),
            now,
            default_account.id,
        )

    assert metric.value == pytest.approx(31.75)
    assert metric.detail["start"] == 369
    assert metric.detail["end"] == 496
    assert metric.detail["days"] == pytest.approx(4.0)
    assert metric.detail["partial_history"] is True


def test_follower_velocity_keeps_seven_day_window_when_history_exists(
    configured_test_db, default_account
):
    from threads_analytics.db import session_scope
    from threads_analytics.metrics import METRIC_FOLLOWER_VELOCITY, compute_metric
    from threads_analytics.models import Run

    now = datetime(2026, 4, 27, 8, 0, tzinfo=timezone.utc)
    older = now - timedelta(days=10)

    with session_scope() as session:
        first = Run(account_id=default_account.id, started_at=older, status="complete")
        latest = Run(account_id=default_account.id, started_at=now, status="complete")
        session.add_all([first, latest])
        session.flush()
        _add_account_snapshot(session, default_account.id, first.id, 300, older)
        _add_account_snapshot(session, default_account.id, latest.id, 496, now)

        metric = compute_metric(
            session,
            METRIC_FOLLOWER_VELOCITY,
            now - timedelta(days=14),
            now,
            default_account.id,
        )

    assert metric.value == pytest.approx(28.0)
    assert metric.detail["days"] == pytest.approx(7.0)
    assert metric.detail["partial_history"] is False
