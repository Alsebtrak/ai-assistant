import hmac
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import storage
from app.config import settings
from app.db import SessionLocal, init_db
from app.memory import ask
from app.models import Document, Entity, Measurement

MAX_FILE = 20 * 1024 * 1024


def auth(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {settings.web_token}"
    if (
        not settings.web_token
        or not authorization
        or not hmac.compare_digest(authorization, expected)
    ):
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.arq.aclose()


app = FastAPI(title="AI Assistant", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class DocumentOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    summary: str | None
    doc_date: date | None
    created_at: datetime


class MeasurementOut(BaseModel):
    name: str
    category: str | None
    value: float | None
    unit: str | None
    measured_at: date


class EntityOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    attributes: dict


@app.get("/health")
async def health() -> dict:
    async with SessionLocal() as session:
        await session.execute(select(1))
    return {"status": "ok"}


@app.get("/documents", dependencies=[Depends(auth)])
async def list_documents() -> list[DocumentOut]:
    async with SessionLocal() as session:
        stmt = (
            select(Document)
            .where(Document.user_id == settings.owner_telegram_id)
            .order_by(Document.created_at.desc())
            .limit(50)
        )
        documents = (await session.scalars(stmt)).all()
    return [DocumentOut.model_validate(doc, from_attributes=True) for doc in documents]


@app.post("/documents", dependencies=[Depends(auth)])
async def upload_document(file: Annotated[UploadFile, File()]) -> dict:
    data = await file.read()
    if len(data) > MAX_FILE:
        raise HTTPException(status_code=413, detail="file too large")

    mime = file.content_type or "application/octet-stream"
    kind = "pdf" if mime == "application/pdf" else "photo"
    extension = "pdf" if kind == "pdf" else "jpg"
    document_id = uuid.uuid4()
    key = f"{settings.owner_telegram_id}/{document_id}.{extension}"

    await storage.put(key, data, mime)
    async with SessionLocal() as session:
        session.add(
            Document(
                id=document_id,
                user_id=settings.owner_telegram_id,
                kind=kind,
                mime=mime,
                s3_key=key,
            )
        )
        await session.commit()

    await app.state.arq.enqueue_job("process_document", str(document_id))
    return {"id": str(document_id), "status": "pending"}


@app.get("/measurements", dependencies=[Depends(auth)])
async def list_measurements(
    name: str | None = None, category: str | None = None
) -> list[MeasurementOut]:
    async with SessionLocal() as session:
        stmt = select(Measurement).where(Measurement.user_id == settings.owner_telegram_id)
        if name:
            stmt = stmt.where(Measurement.name == name)
        if category:
            stmt = stmt.where(Measurement.category == category)
        stmt = stmt.order_by(Measurement.measured_at.desc()).limit(500)
        rows = (await session.scalars(stmt)).all()
    return [
        MeasurementOut(
            name=row.name,
            category=row.category,
            value=float(row.value) if row.value is not None else None,
            unit=row.unit,
            measured_at=row.measured_at,
        )
        for row in rows
    ]


@app.get("/entities", dependencies=[Depends(auth)])
async def list_entities() -> list[EntityOut]:
    async with SessionLocal() as session:
        stmt = (
            select(Entity)
            .where(Entity.user_id == settings.owner_telegram_id)
            .order_by(Entity.kind, Entity.name)
        )
        rows = (await session.scalars(stmt)).all()
    return [EntityOut.model_validate(row, from_attributes=True) for row in rows]


@app.post("/ask", dependencies=[Depends(auth)])
async def ask_endpoint(payload: AskRequest) -> dict:
    async with SessionLocal() as session:
        answer, documents = await ask(session, settings.owner_telegram_id, payload.question)
    return {
        "answer": answer,
        "sources": [
            {"id": str(doc.id), "summary": doc.summary, "date": str(doc.doc_date or "")}
            for doc in documents
        ],
    }
