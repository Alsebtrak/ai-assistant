from io import BytesIO

from openpyxl import Workbook

from app.sheets import xlsx_to_text


def _workbook(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Анализы"
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_xlsx_contains_all_rows():
    data = _workbook([["Показатель", "01.10", "01.11"], ["Гемоглобин", 150, 148]])
    text = xlsx_to_text(data)
    assert "Лист «Анализы»" in text
    assert "Гемоглобин | 150 | 148" in text


def test_xlsx_handles_empty_sheet():
    workbook = Workbook()
    buffer = BytesIO()
    workbook.save(buffer)
    assert xlsx_to_text(buffer.getvalue()) == ""


def test_xlsx_truncates_huge_sheet_by_focus():
    rows = [["Показатель", "Дата"]] + [[f"Строка {i}", i] for i in range(3000)]
    rows.append(["Назаров Алексей", "важно"])
    text = xlsx_to_text(_workbook(rows))
    assert "Назаров Алексей" in text
    assert len(text) <= 20000
