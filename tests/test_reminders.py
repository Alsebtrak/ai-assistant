from datetime import UTC, datetime, timedelta

from app.reminders import next_due

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def test_one_off_returns_none():
    assert next_due(NOW - timedelta(hours=1), None, NOW) is None


def test_repeat_daily_advances_to_tomorrow():
    due = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
    assert next_due(due, 1, NOW) == datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def test_repeat_weekly():
    due = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert next_due(due, 7, NOW) == datetime(2026, 11, 22, 9, 0, tzinfo=UTC)


def test_zero_interval_is_one_off():
    assert next_due(NOW, 0, NOW) is None
