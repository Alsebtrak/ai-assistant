"""Маршрутизация входящих сообщений: вопрос, ссылка, «запомни», выборка.

Классификация — по простым признакам, без LLM: предсказуемо и дешево.
LLM используется только внутри обработчиков (извлечь параметры, сжать текст).
"""

import ipaddress
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.extract import parse_json
from app.memory import index_document
from app.models import Document, Fact, Measurement, Reminder
from app.prompts import ANSWER_STYLE

URL_RE = re.compile(r"https?://[^\s<>\"']+")
REMEMBER_RE = re.compile(r"^\s*(запомни|запиши|сохрани)\b[:,]?\s*(.*)$", re.IGNORECASE | re.DOTALL)
REMIND_RE = re.compile(r"^\s*(напомни|напоминание|не забудь)\b", re.IGNORECASE)
SEARCH_RE = re.compile(
    r"\b(найди|поищи|узнай|загугли|посмотри в интернете|сколько нужно|какие нормативы|"
    r"какие правила|что говорят)\b",
    re.IGNORECASE,
)
QUERY_RE = re.compile(
    r"\b(покажи|показать|динамик\w*|сколько|график|выборк\w*|за период|за месяц|за год|"
    r"последни\w*|все результаты|статистик\w*)\b",
    re.IGNORECASE,
)
CHART_RE = re.compile(r"\b(график\w*|динамик\w*|тренд\w*)\b", re.IGNORECASE)
SHOT_RE = re.compile(
    r"\b(стрел\w*|серия|серии|мишен\w*|дистанц\w*|очк\w*|3d|лук\w*)\b", re.IGNORECASE
)
STATS_RE = re.compile(
    r"\b(статистик\w*|итоги|средн\w*|лучш\w*|прогресс\w*|рекорд\w*)\b", re.IGNORECASE
)
QUESTION_WORDS_RE = re.compile(
    r"\?|сколько|какой|какая|какие|покажи|график|напомни|запомни|найди|что такое",
    re.IGNORECASE,
)
MAX_PAGE = 2_000_000
MSK = ZoneInfo("Europe/Moscow")


def classify(text: str) -> str:
    if URL_RE.search(text):
        return "link"
    if REMIND_RE.search(text):
        return "remind"
    if REMEMBER_RE.match(text):
        return "remember"
    if SEARCH_RE.search(text):
        return "search"
    if CHART_RE.search(text):
        return "chart"
    if STATS_RE.search(text) and SHOT_RE.search(text):
        return "shot_stats"
    if QUERY_RE.search(text):
        return "query"
    if SHOT_RE.search(text) and re.search(r"\d", text):
        return "shot"
    if (
        len(text) <= 80
        and re.search(r"\d", text)
        and not QUESTION_WORDS_RE.search(text)
    ):
        return "metric"
    return "question"


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text)
    return match.group(0).rstrip(".,);") if match else None


def is_safe_url(url: str) -> bool:
    """Запрет локальных адресов: бот не должен ходить внутрь сети (SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


async def metric_rows(
    session: AsyncSession, user_id: int, text: str
) -> list[Measurement] | str:
    """LLM вытаскивает метрику и период, SQL — данные.

    Возвращает список измерений или строку с объяснением, почему пусто.
    """
    raw = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "Извлеки из запроса метрику и период. Ответ — только JSON: "
                    '{"metric": "название показателя или null", '
                    '"date_from": "YYYY-MM-DD или null", "date_to": "YYYY-MM-DD или null"}. '
                    "Сегодня " + date.today().isoformat() + "."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=300,
    )
    params = parse_json(raw)

    stmt = select(Measurement).where(Measurement.user_id == user_id)
    metric = (params.get("metric") or "").strip()
    if metric:
        stmt = stmt.where(Measurement.name.ilike(f"%{metric}%"))
    try:
        if params.get("date_from"):
            stmt = stmt.where(Measurement.measured_at >= date.fromisoformat(params["date_from"]))
        if params.get("date_to"):
            stmt = stmt.where(Measurement.measured_at <= date.fromisoformat(params["date_to"]))
    except ValueError:
        pass
    rows = list((await session.scalars(stmt.order_by(Measurement.measured_at).limit(50))).all())

    if rows:
        return rows

    names = (
        await session.scalars(
            select(Measurement.name).where(Measurement.user_id == user_id).distinct().limit(30)
        )
    ).all()
    if names:
        return "Не нашёл по запросу. В памяти есть: " + ", ".join(names)
    return "В памяти пока нет измерений. Пришли фото анализа или импортируй КДЛ."


async def ask_metric(session: AsyncSession, user_id: int, text: str) -> str:
    result = await metric_rows(session, user_id, text)
    if isinstance(result, str):
        return result
    return "\n".join(
        f"{row.measured_at:%d.%m.%Y} — {row.name}: {row.value:g} {row.unit or ''}".strip()
        for row in result
    )


NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def extract_number(text: str) -> float | None:
    match = NUMBER_RE.search(text)
    return float(match.group().replace(",", ".")) if match else None


async def record_metric(session: AsyncSession, user_id: int, text: str) -> str:
    value = extract_number(text)
    if value is None:
        return "Не вижу числа. Напиши так: «вес 80 кг» или «давление 120/80»."

    raw = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "Определи, что за измерение в сообщении: название, единицу и категорию. "
                    "Число не придумывай и не меняй — его подставят отдельно. Ответ — только JSON: "
                    '{"name": "название", "unit": "единица или null", "category": "группа"}. '
                    "category — короткая группа: медицина, спорт, стрельба из лука, питомцы, "
                    "растения, другое. Если это не измерение — {\"name\": null}."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=200,
    )
    data = parse_json(raw)
    name = str(data.get("name") or "").strip()[:200]
    if not name:
        return "Не понял, что записать. Напиши так: «вес 80 кг» или «давление 120/80»."

    unit = str(data.get("unit") or "").strip()[:32] or None
    category = str(data.get("category") or "другое").strip()[:100] or None
    session.add(
        Measurement(
            user_id=user_id,
            source="manual",
            name=name,
            category=category,
            value=Decimal(str(value)),
            unit=unit,
            measured_at=date.today(),
        )
    )
    await session.commit()
    return f"Записал: {name} {value:g} {unit or ''}".strip()


async def record_shot(session: AsyncSession, user_id: int, text: str) -> str:
    raw = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "Извлеки данные серии стрельбы из лука. Ответ — только JSON: "
                    '{"points": число|null, "distance_m": число|null, "arrows": число|null, '
                    '"discipline": "3D|длинный лук|классический|блочный|null"}. '
                    "points — очки серии (сумма), distance_m — дистанция в метрах, "
                    "arrows — число стрел. Не выдумывай: чего нет — null."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=200,
    )
    data = parse_json(raw)
    points = data.get("points")
    if points is None:
        points = extract_number(text)
    if points is None:
        return "Не вижу очков серии. Напиши так: «серия 108, 18 м, 12 стрел»."

    meta = {
        "distance_m": data.get("distance_m"),
        "arrows": data.get("arrows"),
        "discipline": data.get("discipline"),
    }
    session.add(
        Measurement(
            user_id=user_id,
            source="manual",
            name="Серия",
            category="стрельба из лука",
            value=Decimal(str(points)),
            unit="очков",
            meta={key: value for key, value in meta.items() if value is not None},
            measured_at=date.today(),
        )
    )
    await session.commit()

    details = []
    if meta["distance_m"]:
        details.append(f"{meta['distance_m']:g} м")
    if meta["arrows"]:
        details.append(f"{meta['arrows']:g} стрел")
    if meta["discipline"]:
        details.append(str(meta["discipline"]))
    suffix = f" ({', '.join(details)})" if details else ""
    return f"Записал серию: {points:g} очков{suffix}"


def format_shot_stats(rows: list[Measurement]) -> str:
    if not rows:
        return "Серий пока нет. Напиши так: «серия 108, 18 м, 12 стрел»."

    values = [float(row.value) for row in rows if row.value is not None]
    if not values:
        return "В сериях нет числовых значений."

    lines = [
        f"Серий: {len(values)}",
        f"Средний: {sum(values) / len(values):.1f}",
        f"Лучший: {max(values):g}",
        f"Худший: {min(values):g}",
    ]

    by_distance: dict[str, list[float]] = {}
    for row in rows:
        distance = (row.meta or {}).get("distance_m")
        if distance is None:
            continue
        by_distance.setdefault(f"{float(distance):g} м", []).append(float(row.value))

    if by_distance:
        lines.append("")
        lines.append("По дистанциям:")
        for distance, scores in sorted(by_distance.items()):
            lines.append(
                f"• {distance}: {len(scores)} серий, средний {sum(scores) / len(scores):.1f}, "
                f"лучший {max(scores):g}"
            )
    return "\n".join(lines)


async def shot_stats(session: AsyncSession, user_id: int) -> str:
    stmt = (
        select(Measurement)
        .where(
            Measurement.user_id == user_id,
            Measurement.category.ilike("%стрельба%"),
        )
        .order_by(Measurement.measured_at)
        .limit(500)
    )
    rows = list((await session.scalars(stmt)).all())
    return format_shot_stats(rows)


async def remember(session: AsyncSession, user_id: int, text: str) -> str:
    raw = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "Преврати сообщение человека в устойчивые факты о нём. "
                    'Ответ — только JSON: {"facts": [{"key": "...", "value": "..."}]}. '
                    "key — короткая категория: аллергия, препарат, цель, предпочтение, "
                    "питомец, растение, техника. Если фактов нет — пустой список."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=500,
    )
    data = parse_json(raw)
    saved = 0
    for item in data.get("facts") or []:
        key = str(item.get("key") or "").strip()[:100]
        value = str(item.get("value") or "").strip()[:1000]
        if not key or not value:
            continue
        exists = await session.scalar(
            select(Fact.id).where(
                Fact.user_id == user_id, Fact.key == key, Fact.value == value
            )
        )
        if exists:
            continue
        session.add(Fact(user_id=user_id, key=key, value=value))
        saved += 1
    await session.commit()
    return f"Запомнил фактов: {saved}." if saved else "Не понял, что именно запомнить."


async def ingest_link(session: AsyncSession, user_id: int, url: str) -> str:
    if not is_safe_url(url):
        return "Эту ссылку открывать не буду: локальные и служебные адреса запрещены."

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return f"Не смог открыть ссылку: {exc}"

    if response.status_code >= 400:
        return f"Сайт ответил {response.status_code}."
    if len(response.content) > MAX_PAGE:
        return "Страница слишком большая — сохрани фрагмент текстом."

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else url
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()
    if len(text) < 50:
        return "На странице нет текста для сохранения."

    document = Document(
        user_id=user_id,
        kind="link",
        status="processed",
        raw_text=text[:50000],
        summary=f"{title} — {url}"[:500],
        extracted={"title": title, "url": url},
    )
    session.add(document)
    await session.flush()
    await index_document(session, document)
    await session.commit()

    digest = await llm.chat(
        [
            {"role": "system", "content": ANSWER_STYLE + " Сожми страницу в 3-5 предложений."},
            {"role": "user", "content": text[:8000]},
        ],
        max_tokens=400,
    )
    return f"📄 {title}\n\n{digest}\n\nСохранил в память."


async def remind(session: AsyncSession, user_id: int, text: str) -> str:
    now = datetime.now(MSK)
    raw = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "Извлеки из просьбы текст напоминания, момент времени и повтор. "
                    "Ответ — только JSON: "
                    '{"text": "что напомнить", "due_at": "YYYY-MM-DDTHH:MM:SS", '
                    '"repeat_days": null}. '
                    f"Сейчас {now:%Y-%m-%d %H:%M}, часовой пояс Europe/Moscow. "
                    "Если время не указано — due_at: null. Если напоминание повторяется "
                    "(каждый день — 1, каждую неделю — 7, каждые 3 дня — 3), укажи repeat_days "
                    "числом, иначе null."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=300,
    )
    data = parse_json(raw)
    reminder_text = str(data.get("text") or text)[:500]
    due_raw = data.get("due_at")
    if not due_raw:
        return "Не понял, на когда напомнить. Скажи, например: «напомни завтра в 10:00 …»."

    try:
        due = datetime.fromisoformat(str(due_raw))
    except ValueError:
        return "Не разобрал дату. Напиши так: «напомни 20.11 в 9:00 …»."
    if due.tzinfo is None:
        due = due.replace(tzinfo=MSK)
    if due <= now:
        due = now + timedelta(minutes=1)

    repeat_days = data.get("repeat_days")
    repeat_days = int(repeat_days) if isinstance(repeat_days, (int, float)) and repeat_days >= 1 else None

    session.add(
        Reminder(
            user_id=user_id,
            text=reminder_text,
            due_at=due.astimezone(UTC),
            repeat_days=repeat_days,
        )
    )
    await session.commit()

    suffix = f", повтор каждые {repeat_days} дн." if repeat_days else ""
    return f"⏰ Напомню {due:%d.%m в %H:%M}: {reminder_text}{suffix}"


async def wiki_lookup(query: str) -> tuple[str, str] | None:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": 1,
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "format": "json",
        "utf8": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get("https://ru.wikipedia.org/w/api.php", params=params)
        pages = response.json().get("query", {}).get("pages", {})
    except (httpx.HTTPError, ValueError):
        return None
    for page in pages.values():
        extract = (page.get("extract") or "").strip()
        if extract:
            return page.get("title", query), extract
    return None


async def search_answer(query: str) -> str:
    found = await wiki_lookup(query)
    context = f"Wikipedia «{found[0]}»: {found[1][:3000]}" if found else "—"
    try:
        answer = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        ANSWER_STYLE
                        + " Ответь на вопрос. Если дан контекст из Wikipedia — используй его, "
                        "но проверяй на здравый смысл. Если контекста нет — ответь из своих "
                        "знаний. Не выдумывай точных цифр, в которых не уверен."
                    ),
                },
                {"role": "user", "content": f"Контекст:\n{context}\n\nВопрос: {query}"},
            ],
            max_tokens=2000,
        )
    except llm.LLMError:
        if found:
            return f"Нашёл в Wikipedia «{found[0]}»:\n\n{found[1][:1500]}"
        return "Поиск ничего не дал, а модель не смогла ответить. Попробуй переформулировать."
    if found:
        answer += f"\n\nИсточник: Wikipedia — «{found[0]}»"
    return answer
