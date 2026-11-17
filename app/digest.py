"""Утренний дайджест."""

from datetime import datetime

from app.models import Document, Reminder


def build_digest(
    reminders: list[Reminder], documents: list[Document], today: datetime
) -> str:
    lines = [f"☀️ {today:%d.%m.%Y}", ""]

    if reminders:
        lines.append("Напоминания:")
        for reminder in reminders:
            lines.append(f"• {reminder.due_at:%H:%M} — {reminder.text}")
    else:
        lines.append("Напоминаний на сегодня нет.")

    if documents:
        lines.append("")
        lines.append("Новое в памяти:")
        for document in documents[:5]:
            summary = (document.summary or document.kind or "").strip()
            lines.append(f"• {summary[:90]}")

    return "\n".join(lines)
