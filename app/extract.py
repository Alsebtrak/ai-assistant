import base64
import json
import re
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from app import llm
from app.config import settings


class MeasurementIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    entity: str | None = Field(default=None, max_length=200)
    value: float | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    measured_at: date | None = None


class FactIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=1000)
    entity: str | None = Field(default=None, max_length=200)


class EntityIn(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    attributes: dict[str, str] = {}


class ExtractedDocument(BaseModel):
    title: str | None = None
    doc_type: str = Field(default="other", max_length=50)
    doc_date: date | None = None
    source: str | None = None
    summary: str = ""
    text: str = ""
    entities: list[EntityIn] = []
    measurements: list[MeasurementIn] = []
    facts: list[FactIn] = []


class ExtractError(RuntimeError):
    pass


SCHEMA = """{
  "title": "название документа или null",
  "doc_type": "medical | sport | hobby | finance | travel | other",
  "doc_date": "YYYY-MM-DD или null",
  "source": "учреждение, клуб, приложение или null",
  "summary": "краткое содержание в 1-3 предложениях",
  "text": "полный текст документа, как он написан, с сохранением строк",
  "entities": [
    {"kind": "паук", "name": "Brachypelma hamorii", "attributes": {"пол": "самка", "возраст": "3 года"}},
    {"kind": "террариум", "name": "Террариум №2", "attributes": {"объём": "30x30x40"}},
    {"kind": "растение", "name": "Фикус лировидный", "attributes": {"горшок": "12 л"}}
  ],
  "measurements": [
    {"name": "Гемоглобин", "category": "медицина", "entity": null, "value": 148.0, "unit": "г/л", "ref_low": 130.0, "ref_high": 160.0, "measured_at": "YYYY-MM-DD"},
    {"name": "Серия из 12 стрел", "category": "стрельба из лука", "entity": "Лук Olympic", "value": 108.0, "unit": "очков", "ref_low": null, "ref_high": null, "measured_at": "YYYY-MM-DD"},
    {"name": "Влажность", "category": "террариум", "entity": "Террариум №2", "value": 78.0, "unit": "%", "ref_low": 70.0, "ref_high": 85.0, "measured_at": "YYYY-MM-DD"}
  ],
  "facts": [
    {"key": "аллергия", "value": "пенициллин", "entity": null},
    {"key": "лук", "value": "Olympic recurve, 42 lb, плечи WNS", "entity": "Лук Olympic"},
    {"key": "кормление", "value": "раз в 10 дней, сверчок", "entity": "Brachypelma hamorii"}
  ]
}"""

SYSTEM = f"""Ты извлекаешь данные из личных документов пользователя. Это могут быть
медицинские анализы и заключения, спортивные протоколы и тренировки, результаты
соревнований (в том числе стрельба из лука), технические паспорта, чеки, поездки —
любые документы, которые человек хочет сохранить в личной памяти.

Правила:
- Верни только JSON, без markdown, без пояснений.
- Не выдумывай значения: если данных нет — null или пустой список.
- measurements — любые числовые показатели с единицами: анализы, пульс, время,
  дистанция, очки, вес, калории. category — короткая группа, чтобы графики не
  смешивали разное: "медицина", "стрельба из лука", "бег", "финансы".
  ref_low/ref_high — только если в документе явно указана норма.
- facts — устойчивые сведения о человеке и его вещах: аллергии, диагнозы,
  препараты, спортивный инвентарь, предпочтения, цели, ограничения.
  key — короткое имя категории ("аллергия", "лук", "цель", "предпочтение").
- Сохраняй полный текст в поле text, включая даты, подписи и примечания.

Схема ответа:
{SCHEMA}"""


def parse_json(raw: str) -> dict:
    """Extract the first JSON object from a model answer."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ExtractError("В ответе модели нет JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExtractError(f"Некорректный JSON: {exc}") from exc


def parse_document(raw: str) -> ExtractedDocument:
    try:
        return ExtractedDocument.model_validate(parse_json(raw))
    except ValidationError as exc:
        raise ExtractError(f"Схема не совпала: {exc.errors()[:3]}") from exc


async def extract_from_text(text: str) -> ExtractedDocument:
    raw = await llm.chat(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Документ:\n\n{text[:20000]}"},
        ],
        model=settings.llm_model,
        max_tokens=4000,
    )
    return parse_document(raw)


async def extract_from_table(text: str) -> ExtractedDocument:
    """Таблицы: строки — показатели, столбцы — даты."""
    raw = await llm.chat(
        [
            {
                "role": "system",
                "content": SYSTEM
                + "\n\nЭто выгрузка из таблицы. Строки обычно показатели, столбцы — даты "
                "или наоборот; шапки могут повторяться на каждом листе. Собери измерения "
                "по датам, не пропускай строки с фамилией пользователя. Если дата в шапке "
                "столбца — используй её для значений этого столбца.",
            },
            {"role": "user", "content": f"Таблица:\n\n{text[:20000]}"},
        ],
        model=settings.llm_model,
        max_tokens=6000,
    )
    return parse_document(raw)


async def extract_from_image(data: bytes, mime: str) -> ExtractedDocument:
    encoded = base64.b64encode(data).decode()
    raw = await llm.chat(
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Распознай документ на фото."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{encoded}"},
                    },
                ],
            },
        ],
        model=settings.llm_vision_model,
        max_tokens=4000,
    )
    return parse_document(raw)
