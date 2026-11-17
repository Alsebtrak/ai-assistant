import io
import logging
import uuid
from datetime import UTC, datetime
from typing import ClassVar

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from arq import cron
from arq.connections import RedisSettings
from pypdf import PdfReader
from sqlalchemy import select

from app import storage
from app.config import settings
from app.db import SessionLocal
from app.extract import ExtractError, extract_from_image, extract_from_text
from app.memory import index_document
from app.models import Document, Reminder
from app.telegram import make_bot
from app.text import split_message

log = logging.getLogger(__name__)


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def describe(document: Document) -> str:
    data = document.extracted or {}
    lines = [f"📄 {data.get('title') or document.kind}", ""]
    if data.get("summary"):
        lines.append(data["summary"])
        lines.append("")

    entities = data.get("entities") or []
    if entities:
        lines.append("Объекты: " + ", ".join(f"{e['kind']} «{e['name']}»" for e in entities[:10]))
        lines.append("")

    measurements = data.get("measurements") or []
    if measurements:
        lines.append("Данные:")
        for item in measurements[:20]:
            value = item.get("value")
            unit = item.get("unit") or ""
            reference = ""
            if item.get("ref_low") is not None and item.get("ref_high") is not None:
                reference = f" (норма {item['ref_low']}–{item['ref_high']})"
            target = f" [{item['entity']}]" if item.get("entity") else ""
            lines.append(f"• {item['name']}{target}: {value} {unit}{reference}".rstrip())
        if len(measurements) > 20:
            lines.append(f"… и ещё {len(measurements) - 20}")

    facts = data.get("facts") or []
    if facts:
        lines.append("")
        lines.append("Факты: " + ", ".join(f"{f['key']}: {f['value']}" for f in facts[:10]))

    lines.append("")
    lines.append("Сохранить в память?")
    return "\n".join(lines)


async def notify(document: Document, text: str, with_buttons: bool) -> None:
    if not settings.telegram_bot_token:
        return
    bot = make_bot()
    try:
        markup = None
        if with_buttons:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Сохранить", callback_data=f"save:{document.id}"
                        ),
                        InlineKeyboardButton(
                            text="🗑 Удалить", callback_data=f"drop:{document.id}"
                        ),
                    ]
                ]
            )
        parts = split_message(text)
        for index, part in enumerate(parts):
            await bot.send_message(
                document.user_id,
                part,
                reply_markup=markup if index == len(parts) - 1 else None,
            )
    except Exception:
        log.exception("telegram notify failed")
    finally:
        await bot.session.close()


async def process_document(ctx, document_id: str) -> None:
    async with SessionLocal() as session:
        document = await session.get(Document, uuid.UUID(document_id))
        if document is None or document.status != "pending":
            return

        try:
            if not document.s3_key:
                raise ExtractError("Файл не сохранён")
            data = await storage.get(document.s3_key)

            if document.kind == "pdf":
                text = pdf_text(data)
                if len(text) < 20:
                    raise ExtractError("В PDF нет текстового слоя — пришлите фото страницы")
                extracted = await extract_from_text(text)
            else:
                extracted = await extract_from_image(data, document.mime or "image/jpeg")
        except ExtractError as exc:
            document.status = "failed"
            document.error = str(exc)
            await session.commit()
            await notify(document, f"⚠️ Не смог разобрать документ: {exc}", with_buttons=False)
            return
        except Exception as exc:
            log.exception("document processing failed")
            document.status = "failed"
            document.error = str(exc)[:500]
            await session.commit()
            await notify(document, "⚠️ Ошибка при обработке. Попробуй позже.", with_buttons=False)
            return

        document.extracted = extracted.model_dump(mode="json")
        document.raw_text = extracted.text
        document.summary = extracted.summary
        document.doc_date = extracted.doc_date
        document.status = "processed"
        document.processed_at = datetime.now(UTC)
        await index_document(session, document)
        await session.commit()

        await notify(document, describe(document), with_buttons=True)


async def check_reminders(ctx) -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(Reminder).where(
                        Reminder.sent_at.is_(None), Reminder.due_at <= now
                    )
                )
            ).all()
        )
        if not rows:
            return

        bot = make_bot()
        try:
            for reminder in rows:
                await bot.send_message(reminder.user_id, f"⏰ {reminder.text}")
                reminder.sent_at = now
            await session.commit()
        finally:
            await bot.session.close()


class WorkerSettings:
    functions: ClassVar = [process_document, check_reminders]
    cron_jobs: ClassVar = [cron(check_reminders, minute=set(range(60)))]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
