"""Расчёт следующего срабатывания повторяющегося напоминания."""

from datetime import datetime, timedelta


def next_due(
    due_at: datetime, repeat_days: int | None, now: datetime
) -> datetime | None:
    """Следующий момент после now, кратный интервалу. None для одноразового."""
    if not repeat_days or repeat_days < 1:
        return None
    step = timedelta(days=repeat_days)
    next_at = due_at
    while next_at <= now:
        next_at += step
    return next_at
