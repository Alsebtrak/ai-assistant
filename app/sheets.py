"""Чтение Excel-таблиц: превращаем листы в компактный текст для LLM."""

import io

from openpyxl import load_workbook

MAX_CHARS = 20000
MAX_ROWS = 4000


def _sheet_lines(sheet) -> list[str]:
    lines: list[str] = []
    for index, row in enumerate(sheet.iter_rows(values_only=True)):
        if index >= MAX_ROWS:
            break
        cells = ["" if value is None else str(value).strip() for value in row]
        line = " | ".join(cells).strip(" |")
        if line:
            lines.append(line)
    return lines


def xlsx_to_text(data: bytes, focus: str = "Назаров") -> str:
    """Текст всех листов. Если не влезает — оставляем шапки и строки с focus."""
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets: list[tuple[str, list[str]]] = []
        for sheet in workbook.worksheets:
            lines = _sheet_lines(sheet)
            if lines:
                sheets.append((sheet.title, lines))

        def render(items: list[tuple[str, list[str]]]) -> str:
            return "\n\n".join(f"Лист «{title}»:\n" + "\n".join(lines) for title, lines in items)

        full = render(sheets)
        if len(full) <= MAX_CHARS:
            return full

        focused = [
            (title, lines[:3] + [line for line in lines[3:] if focus.lower() in line.lower()])
            for title, lines in sheets
        ]
        text = render(focused)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS].rsplit("\n", 1)[0]
        return text
    finally:
        workbook.close()
