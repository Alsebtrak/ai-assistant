"""Импорт результатов анализов из личного кабинета КДЛ.

КДЛ отдаёт HTML-фрагменты через `POST /api.php?action=Profile/Order`.
Для входа нужна живая сессия (телефон + дата рождения + капча + SMS), поэтому
куки экспортируются из браузера один раз и передаются в `--cookies`.

    python -m app.kdl sync --cookies .secrets/kdl_cookies.json

Файл куки — JSON вида {"PHPSESSID": "..."}.
"""

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Measurement

BASE = "https://kdl.ru"


@dataclass
class OrderRef:
    number: str
    internal_id: str


@dataclass
class ParsedResult:
    order: str
    measured_at: date
    name: str
    code: str | None
    value: float
    unit: str | None
    ref_low: float | None
    ref_high: float | None

    @property
    def external_id(self) -> str:
        return f"{self.order}:{self.code or self.name}"


NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
RANGE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*[-–]\s*(-?\d+(?:[.,]\d+)?)\s*$")
UPPER = re.compile(r"^\s*<\s*(-?\d+(?:[.,]\d+)?)\s*$")
LOWER = re.compile(r"^\s*>\s*(-?\d+(?:[.,]\d+)?)\s*$")
DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _number(text: str) -> float | None:
    match = NUMBER.search(text.replace(",", "."))
    return float(match.group()) if match else None


def parse_reference(text: str) -> tuple[float | None, float | None]:
    text = text.strip()
    if match := RANGE.match(text):
        return _number(match.group(1)), _number(match.group(2))
    if match := UPPER.match(text):
        return None, _number(match.group(1))
    if match := LOWER.match(text):
        return _number(match.group(1)), None
    return None, None


def parse_profile(html: str) -> list[OrderRef]:
    soup = BeautifulSoup(html, "html.parser")
    orders: list[OrderRef] = []
    seen: set[str] = set()
    for link in soup.select("a[data-id]"):
        number = _clean(link.get_text())
        internal_id = link.get("data-id") or ""
        if number.isdigit() and internal_id and internal_id not in seen:
            seen.add(internal_id)
            orders.append(OrderRef(number=number, internal_id=internal_id))
    return orders


def parse_order(html: str) -> list[ParsedResult]:
    soup = BeautifulSoup(html, "html.parser")

    title = soup.select_one(".profile-order__title")
    if title is None:
        return []
    order_match = re.search(r"№\s*(\d+)", title.get_text())
    if not order_match:
        return []
    order = order_match.group(1)

    date_cell = soup.select_one(".profile-order__date")
    date_match = DATE.search(date_cell.get_text()) if date_cell else None
    if not date_match:
        return []
    day, month, year = (int(part) for part in date_match.groups())
    measured_at = date(year, month, day)

    results: list[ParsedResult] = []
    for row in soup.select(".profile-order__row._body"):
        name_cell = row.select_one(".profile-order__cell._name")
        value_cell = row.select_one(".profile-order__cell._value")
        if name_cell is None or value_cell is None:
            continue

        name = _clean(name_cell.get_text())
        if not name:
            continue

        code_input = name_cell.select_one("input[name=test]")
        code = code_input.get("value") if code_input else None

        value_text = value_cell.get_text()
        if "<" in value_text or ">" in value_text:
            continue  # «<6.0» — не точное число, не искажаем график
        value = _number(value_text)
        if value is None:
            continue  # «не обнаружено», «отрицательный» и т.п.

        normal_cell = row.select_one(".profile-order__cell._normal")
        unit_cell = row.select_one(".profile-order__cell._dimension")
        ref_low, ref_high = parse_reference(normal_cell.get_text() if normal_cell else "")

        results.append(
            ParsedResult(
                order=order,
                measured_at=measured_at,
                name=name,
                code=code,
                value=value,
                unit=_clean(unit_cell.get_text()) if unit_cell else None,
                ref_low=ref_low,
                ref_high=ref_high,
            )
        )
    return results


async def fetch_profile(client: httpx.AsyncClient) -> list[OrderRef]:
    response = await client.get(f"{BASE}/profile")
    response.raise_for_status()
    return parse_profile(response.text)


async def fetch_order(client: httpx.AsyncClient, ref: OrderRef) -> list[ParsedResult]:
    response = await client.post(
        f"{BASE}/api.php",
        params={"action": "Profile/Order"},
        data={"id": ref.internal_id, "senderRegion": "5"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        return []
    return parse_order(payload.get("html", ""))


def load_cookies(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "PHPSESSID" not in data:
        raise SystemExit("В файле куки нет PHPSESSID")
    return data


async def import_results(
    session: AsyncSession, user_id: int, results: list[ParsedResult]
) -> int:
    saved = 0
    for item in results:
        existing = await session.scalar(
            select(Measurement).where(
                Measurement.user_id == user_id,
                Measurement.source == "kdl",
                Measurement.external_id == item.external_id,
                Measurement.name == item.name,
            )
        )
        if existing is None:
            session.add(
                Measurement(
                    user_id=user_id,
                    source="kdl",
                    external_id=item.external_id,
                    name=item.name,
                    category="медицина",
                    value=Decimal(str(item.value)),
                    unit=item.unit,
                    ref_low=Decimal(str(item.ref_low)) if item.ref_low is not None else None,
                    ref_high=Decimal(str(item.ref_high)) if item.ref_high is not None else None,
                    measured_at=item.measured_at,
                )
            )
        else:
            existing.value = Decimal(str(item.value))
            existing.unit = item.unit
            existing.ref_low = (
                Decimal(str(item.ref_low)) if item.ref_low is not None else None
            )
            existing.ref_high = (
                Decimal(str(item.ref_high)) if item.ref_high is not None else None
            )
            existing.measured_at = item.measured_at
        saved += 1
    await session.commit()
    return saved


async def sync(cookies: dict[str, str], user_id: int) -> int:
    async with httpx.AsyncClient(
        cookies=cookies, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
    ) as client:
        profile = await client.get(f"{BASE}/profile")
        if "Выйти" not in profile.text:
            raise SystemExit("Сессия КДЛ недействительна: войдите в кабинет и обновите куки")
        refs = parse_profile(profile.text)
        total = 0
        async with SessionLocal() as session:
            for ref in refs:
                results = await fetch_order(client, ref)
                if results:
                    total += await import_results(session, user_id, results)
        return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт анализов КДЛ")
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("--cookies", default=".secrets/kdl_cookies.json", type=Path)
    parser.add_argument("--user", type=int, default=settings.owner_telegram_id)
    args = parser.parse_args()

    cookies = load_cookies(args.cookies)

    async def run() -> None:
        await init_db()
        count = await sync(cookies, args.user)
        print(f"Импортировано показателей: {count}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
