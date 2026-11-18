from datetime import date
from decimal import Decimal

from app.models import Measurement
from app.router import format_shot_stats


def _shot(points: str, distance: str | None = None) -> Measurement:
    return Measurement(
        user_id=1,
        name="Серия",
        category="стрельба из лука",
        value=Decimal(points),
        unit="очков",
        meta={"distance_m": float(distance)} if distance else {},
        measured_at=date(2026, 11, 18),
    )


def test_stats_empty():
    assert "Серий пока нет" in format_shot_stats([])


def test_stats_totals():
    text = format_shot_stats([_shot("108", "18"), _shot("102", "18"), _shot("95")])
    assert "Серий: 3" in text
    assert "Лучший: 108" in text
    assert "Худший: 95" in text
    assert "Средний: 101.7" in text


def test_stats_by_distance():
    text = format_shot_stats([_shot("108", "18"), _shot("96", "18"), _shot("80", "30")])
    assert "18 м: 2 серий" in text
    assert "30 м: 1 серий" in text
