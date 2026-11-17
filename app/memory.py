from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.models import Chunk, Document, Entity, Fact
from app.prompts import ANSWER_STYLE

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text on sentence/paragraph boundaries with a small overlap."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


async def index_document(session: AsyncSession, document: Document) -> None:
    """Re-index a document idempotently: old chunks are replaced, not duplicated."""
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    for part in chunk_text(document.raw_text or ""):
        session.add(
            Chunk(document_id=document.id, user_id=document.user_id, content=part)
        )


async def search_chunks(
    session: AsyncSession, user_id: int, query: str, limit: int = 8
) -> list[Chunk]:
    tsquery = func.plainto_tsquery("russian", query)
    stmt = (
        select(Chunk)
        .where(Chunk.user_id == user_id, Chunk.tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(Chunk.tsv, tsquery).desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def active_facts(session: AsyncSession, user_id: int, limit: int = 30) -> list[Fact]:
    stmt = (
        select(Fact)
        .where(Fact.user_id == user_id, Fact.superseded_at.is_(None))
        .order_by(Fact.created_at.desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def entities(session: AsyncSession, user_id: int, limit: int = 50) -> list[Entity]:
    stmt = (
        select(Entity)
        .where(Entity.user_id == user_id)
        .order_by(Entity.kind, Entity.name)
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def ask(session: AsyncSession, user_id: int, question: str) -> tuple[str, list[Document]]:
    facts = await active_facts(session, user_id)
    objects = await entities(session, user_id)
    chunks = await search_chunks(session, user_id, question)

    profile = "\n".join(f"- {fact.key}: {fact.value}" for fact in facts) or "—"
    objects_block = "\n".join(
        f"- {entity.kind} «{entity.name}»: "
        + ", ".join(f"{key}={value}" for key, value in (entity.attributes or {}).items())
        for entity in objects
    ) or "—"
    context = "\n\n".join(
        f"[{index}] {chunk.content}" for index, chunk in enumerate(chunks, start=1)
    ) or "—"

    answer = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    ANSWER_STYLE
                    + " Ты личный ассистент пользователя. Если вопрос о личных данных "
                    "(здоровье, документы, питомцы, вещи) — опирайся на профиль, объекты "
                    "и фрагменты документов и не выдумывай. Если таких данных нет, честно "
                    "скажи об этом. Если вопрос общий (правила, нормативы, факты, спорт, "
                    "техника) — отвечай из своих знаний, не притворяясь, что это из памяти."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Профиль:\n{profile}\n\nОбъекты:\n{objects_block}\n\n"
                    f"Фрагменты документов:\n{context}\n\nВопрос: {question}"
                ),
            },
        ],
        max_tokens=1500,
    )

    documents: list[Document] = []
    if chunks:
        ids = {chunk.document_id for chunk in chunks}
        found = await session.scalars(select(Document).where(Document.id.in_(ids)))
        documents = list(found.all())
    return answer, documents
