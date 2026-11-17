from datetime import date
from decimal import Decimal

from app.chart import render_chart
from app.models import Measurement


def _row(name: str, value: str, day: int, low: str | None = None, high: str | None = None):
    return Measurement(
        user_id=1,
        name=name,
        value=Decimal(value),
        unit="Ед/л",
        ref_low=Decimal(low) if low else None,
        ref_high=Decimal(high) if high else None,
        measured_at=date(2026, 10, day),
    )


def test_render_chart_returns_png():
    rows = [_row("АЛТ", "48.1", 6, None, "50"), _row("АЛТ", "66.2", 1, None, "50")]
    png = render_chart(rows)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 5000


def test_render_chart_with_reference_band():
    rows = [_row("Гемоглобин", "148", 1, "135", "169"), _row("Гемоглобин", "157", 6, "135", "169")]
    assert render_chart(rows).startswith(b"\x89PNG")


def test_render_chart_requires_rows():
    try:
        render_chart([])
    except ValueError:
        return
    raise AssertionError("ожидали ValueError")
