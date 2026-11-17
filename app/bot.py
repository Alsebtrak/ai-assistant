import logging
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import BaseFilter, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from app import storage
from app.config import settings
from app.db import SessionLocal, init_db
from app.memory import ask
from app.models import Document, Entity, Fact, Measurement
from app.telegram import make_bot

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
    if document.mime_type != "application/pdf":
        await message.answer("Пока понимаю фото и PDF.")
        return
    await handle_file(message, bot, arq, document, "pdf", "application/pdf", "pdf")


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
    await message.answer("Думаю…")
    try:
        async with SessionLocal() as session:
            answer, documents = await ask(session, message.from_user.id, message.text)
    except Exception:
        log.exception("ask failed")
        await message.answer("Не получилось ответить, попробуй ещё раз.")
        return

    sources = [
        f"• {(doc.summary or doc.raw_text or '')[ :80]}…" for doc in documents[:3] if doc
    ]
    text = answer
    if sources:
        text += "\n\nИсточники:\n" + "\n".join(sources)
    await message.answer(text[:4000])


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
                    measured_at=item.get("measured_at") or measured_at,
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
