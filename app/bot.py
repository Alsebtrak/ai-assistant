import logging
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import BaseFilter, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from app import storage
from app.chart import render_chart
from app.config import settings
from app.db import SessionLocal, init_db
from app.memory import ask, index_document
from app.models import Document, Entity, Fact, Measurement
from app.router import (
    ask_metric,
    classify,
    extract_url,
    ingest_link,
    metric_rows,
    record_metric,
    record_shot,
    remember,
    remind,
    search_answer,
    shot_stats,
)
from app.telegram import make_bot
from app.text import split_message

log = logging.getLogger(__name__)
router = Router()

MAX_FILE = 20 * 1024 * 1024


class Allowed(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and event.from_user.id in settings.allowed_ids)


router.message.filter(Allowed())
router.callback_query.filter(Allowed())


def keyboard(document_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data=f"save:{document_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"drop:{document_id}"),
            ]
        ]
    )


def answer_keyboard(document_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💾 Сохранить в память", callback_data=f"note:save:{document_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить из памяти", callback_data=f"note:drop:{document_id}"
                ),
            ]
        ]
    )


async def send_answer(message: Message, answer: str, question: str) -> None:
    async with SessionLocal() as session:
        document = Document(
            user_id=message.from_user.id,
            kind="note",
            status="draft",
            raw_text=answer,
            summary=question[:500],
        )
        session.add(document)
        await session.flush()
        document_id = document.id
        await session.commit()

    parts = split_message(answer)
    for index, part in enumerate(parts):
        markup = answer_keyboard(document_id) if index == len(parts) - 1 else None
        await message.answer(part, reply_markup=markup)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Привет. Пришли фото документа или PDF — распознаю и предложу сохранить. "
        "Или задай вопрос по тому, что уже в памяти."
    )


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot, arq) -> None:
    await handle_file(message, bot, arq, message.photo[-1], "photo", "image/jpeg", "jpg")


@router.message(F.document)
async def on_document(message: Message, bot: Bot, arq) -> None:
    document = message.document
    name = (document.file_name or "").lower()
    if document.mime_type == "application/pdf":
        await handle_file(message, bot, arq, document, "pdf", "application/pdf", "pdf")
        return
    if name.endswith(".xlsx") or document.mime_type in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }:
        await handle_file(
            message,
            bot,
            arq,
            document,
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        )
        return
    await message.answer("Пока понимаю фото, PDF и Excel (.xlsx).")


async def handle_file(
    message: Message, bot: Bot, arq, file, kind: str, mime: str, extension: str
) -> None:
    if file.file_size and file.file_size > MAX_FILE:
        await message.answer("Файл больше 20 МБ — Telegram не отдаст его боту.")
        return

    document_id = uuid.uuid4()
    key = f"{message.from_user.id}/{document_id}.{extension}"

    buffer = await bot.download(file)
    if buffer is None:
        await message.answer("Не смог скачать файл.")
        return
    await storage.put(key, buffer.getvalue(), mime)

    async with SessionLocal() as session:
        session.add(
            Document(
                id=document_id,
                user_id=message.from_user.id,
                kind=kind,
                mime=mime,
                s3_key=key,
            )
        )
        await session.commit()

    await arq.enqueue_job("process_document", str(document_id))
    await message.answer("Принял, разбираю…")


@router.message(F.text)
async def on_text(message: Message) -> None:
    text = message.text or ""
    log.info("ask user=%s len=%s", message.from_user.id, len(text))
    await message.answer("Думаю…")

    try:
        async with SessionLocal() as session:
            intent = classify(text)
            log.info("intent=%s", intent)
            if intent == "link":
                url = extract_url(text)
                answer = (
                    await ingest_link(session, message.from_user.id, url)
                    if url
                    else "Не нашёл ссылку."
                )
            elif intent == "remind":
                answer = await remind(session, message.from_user.id, text)
            elif intent == "remember":
                answer = await remember(session, message.from_user.id, text)
            elif intent == "search":
                await message.answer("Взял в работу 🔎")
                answer = await search_answer(text)
            elif intent == "chart":
                result = await metric_rows(session, message.from_user.id, text)
                if isinstance(result, str):
                    answer = result
                else:
                    png = render_chart(result)
                    caption = f"{result[0].name}: {len(result)} точек"
                    if result[0].unit:
                        caption += f", {result[0].unit}"
                    await message.answer_photo(
                        BufferedInputFile(png, filename="chart.png"), caption=caption
                    )
                    return
            elif intent == "query":
                answer = await ask_metric(session, message.from_user.id, text)
            elif intent == "metric":
                answer = await record_metric(session, message.from_user.id, text)
            elif intent == "shot":
                answer = await record_shot(session, message.from_user.id, text)
            elif intent == "shot_stats":
                answer = await shot_stats(session, message.from_user.id)
            else:
                answer, documents = await ask(session, message.from_user.id, text)
                sources = [
                    f"• {(doc.summary or doc.raw_text or '')[:80]}…"
                    for doc in documents[:3]
                    if doc
                ]
                if sources:
                    answer += "\n\nИсточники:\n" + "\n".join(sources)
    except Exception:
        log.exception("ask failed")
        await message.answer("Не получилось ответить, попробуй ещё раз.")
        return

    answer = (answer or "").strip() or "Не смог сформулировать ответ, попробуй переформулировать вопрос."
    await send_answer(message, answer, text)


@router.callback_query(F.data.startswith("save:"))
async def on_save(callback: CallbackQuery) -> None:
    document_id = uuid.UUID(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None or document.user_id != callback.from_user.id:
            await callback.answer("Документ не найден")
            return
        if document.status != "processed":
            await callback.answer("Уже обработано")
            return

        data = document.extracted or {}
        measured_at = document.doc_date or date.today()

        entity_ids: dict[str, uuid.UUID] = {}
        for item in data.get("entities") or []:
            entity = await session.scalar(
                select(Entity).where(
                    Entity.user_id == document.user_id,
                    Entity.kind == item["kind"],
                    Entity.name == item["name"],
                )
            )
            if entity is None:
                entity = Entity(
                    user_id=document.user_id,
                    kind=item["kind"],
                    name=item["name"],
                    attributes=item.get("attributes") or {},
                )
                session.add(entity)
                await session.flush()
            elif item.get("attributes"):
                entity.attributes = {**(entity.attributes or {}), **item["attributes"]}
            entity_ids[entity.name] = entity.id

        saved = 0
        for item in data.get("measurements") or []:
            if item.get("value") is None:
                continue
            try:
                value = Decimal(str(item["value"]))
                ref_low = Decimal(str(item["ref_low"])) if item.get("ref_low") is not None else None
                ref_high = (
                    Decimal(str(item["ref_high"])) if item.get("ref_high") is not None else None
                )
            except (InvalidOperation, ValueError):
                continue
            raw_date = item.get("measured_at")
            try:
                item_date = date.fromisoformat(raw_date) if raw_date else measured_at
            except ValueError:
                item_date = measured_at
            session.add(
                Measurement(
                    user_id=document.user_id,
                    entity_id=entity_ids.get(item.get("entity") or ""),
                    document_id=document.id,
                    name=item["name"],
                    category=item.get("category"),
                    value=value,
                    unit=item.get("unit"),
                    ref_low=ref_low,
                    ref_high=ref_high,
                    measured_at=item_date,
                )
            )
            saved += 1

        facts = 0
        for item in data.get("facts") or []:
            exists = await session.scalar(
                select(Fact.id).where(
                    Fact.user_id == document.user_id,
                    Fact.key == item["key"],
                    Fact.value == item["value"],
                    Fact.superseded_at.is_(None),
                )
            )
            if exists:
                continue
            session.add(
                Fact(
                    user_id=document.user_id,
                    entity_id=entity_ids.get(item.get("entity") or ""),
                    key=item["key"],
                    value=item["value"],
                    source_document_id=document.id,
                )
            )
            facts += 1

        document.status = "confirmed"
        await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"Сохранено: {saved} показателей, {facts} фактов")


@router.callback_query(F.data.startswith("drop:"))
async def on_drop(callback: CallbackQuery) -> None:
    document_id = uuid.UUID(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document and document.user_id == callback.from_user.id:
            document.status = "declined"
            await session.commit()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("note:save:"))
async def on_note_save(callback: CallbackQuery) -> None:
    document_id = uuid.UUID(callback.data.split(":")[2])
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None or document.user_id != callback.from_user.id:
            await callback.answer("Не найдено")
            return
        if document.status == "processed":
            await callback.answer("Уже в памяти")
            return
        document.status = "processed"
        await index_document(session, document)
        await session.commit()

    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить из памяти", callback_data=f"note:drop:{document_id}"
                    )
                ]
            ]
        )
    )
    await callback.answer("Сохранил в память")


@router.callback_query(F.data.startswith("note:drop:"))
async def on_note_drop(callback: CallbackQuery) -> None:
    document_id = uuid.UUID(callback.data.split(":")[2])
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document and document.user_id == callback.from_user.id:
            await session.delete(document)
            await session.commit()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Удалено из памяти")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()

    bot = make_bot()
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await dispatcher.start_polling(bot, arq=arq)
    finally:
        await arq.aclose()
        await bot.session.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
