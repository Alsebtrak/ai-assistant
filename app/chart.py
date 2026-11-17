"""PNG-графики показателей для Telegram."""

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

from app.models import Measurement


def render_chart(rows: list[Measurement]) -> bytes:
    rows = sorted(rows, key=lambda row: row.measured_at)
    if not rows:
        raise ValueError("нет данных для графика")

    dates = [row.measured_at for row in rows]
    values = [float(row.value) for row in rows]
    name = rows[0].name
    unit = rows[0].unit or ""

    figure, axes = plt.subplots(figsize=(9, 4.5), dpi=110)
    axes.plot(dates, values, color="#2563eb", marker="o", linewidth=2)
    for date, value in zip(dates, values):
        axes.annotate(
            f"{value:g}",
            (date, value),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=9,
        )

    lows = [row.ref_low for row in rows if row.ref_low is not None]
    highs = [row.ref_high for row in rows if row.ref_high is not None]
    if lows and highs:
        axes.axhspan(
            min(float(item) for item in lows),
            max(float(item) for item in highs),
            color="#16a34a",
            alpha=0.08,
            label="норма",
        )
    elif highs:
        axes.axhline(
            max(float(item) for item in highs),
            color="#dc2626",
            linestyle="--",
            linewidth=1,
            label="верхняя граница нормы",
        )

    title = f"{name}, {unit}" if unit else name
    axes.set_title(title)
    axes.grid(alpha=0.25)
    axes.xaxis.set_major_formatter(DateFormatter("%d.%m.%y"))
    figure.autofmt_xdate()
    figure.tight_layout()

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    plt.close(figure)
    return buffer.getvalue()
