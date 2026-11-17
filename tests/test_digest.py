from datetime import UTC, date, datetime

from app.digest import build_digest
from app.models import Document, Reminder

NOW = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)


def _reminder(text: str, hour: int) -> Reminder:
    return Reminder(
        user_id=1,
        text=text,
        due_at=datetime(2026, 11, 18, hour, 0, tzinfo=UTC),
        sent_at=None,
    )


def _document(summary: str) -> Document:
    return Document(user_id=1, kind="photo", status="processed", summary=summary)


def test_digest_without_reminders():
    text = build_digest([], [], NOW)
    assert "Напоминаний на сегодня нет." in text


def test_digest_with_reminders_and_documents():
    text = build_digest(
        [_reminder("полить фикус", 10), _reminder("кормление паука", 14)],
        [_document("Общий анализ крови")],
        NOW,
    )
    assert "10:00 — полить фикус" in text
    assert "14:00 — кормление паука" in text
    assert "Новое в памяти:" in text
    assert "Общий анализ крови" in text
    assert date(2026, 11, 18).strftime("%d.%m.%Y") in text
