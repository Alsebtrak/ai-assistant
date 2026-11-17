import pytest

from app.extract import ExtractError, parse_document, parse_json


def test_parse_json_with_fence():
    raw = '```json\n{"title": "Анализ", "doc_type": "lab"}\n```'
    assert parse_json(raw)["title"] == "Анализ"


def test_parse_json_with_prose():
    raw = 'Вот результат:\n{"doc_type": "lab", "measurements": []}\nГотово.'
    assert parse_json(raw)["doc_type"] == "lab"


def test_parse_json_without_object():
    with pytest.raises(ExtractError):
        parse_json("no json here")


def test_parse_document_full():
    raw = """{
      "title": "Общий анализ крови",
      "doc_type": "lab",
      "doc_date": "2026-11-01",
      "summary": "Всё в норме",
      "text": "Гемоглобин 148",
      "measurements": [
        {"name": "Гемоглобин", "value": 148, "unit": "г/л", "ref_low": 130, "ref_high": 160}
      ],
      "facts": [{"key": "аллергия", "value": "пенициллин"}]
    }"""
    doc = parse_document(raw)
    assert doc.measurements[0].value == 148
    assert doc.measurements[0].measured_at is None
    assert doc.facts[0].key == "аллергия"


def test_parse_document_rejects_bad_schema():
    with pytest.raises(ExtractError):
        parse_document('{"doc_type": "lab", "measurements": [{"value": 1}]}')


def test_parse_document_rejects_unknown_date():
    with pytest.raises(ExtractError):
        parse_document('{"doc_date": "01.11.2026"}')


def test_parse_document_with_entities_and_links():
    raw = """{
      "title": "Террариум №2",
      "doc_type": "hobby",
      "entities": [
        {"kind": "паук", "name": "B. hamorii", "attributes": {"пол": "самка"}}
      ],
      "measurements": [
        {"name": "Влажность", "category": "террариум", "entity": "Террариум №2",
         "value": 78, "unit": "%", "ref_low": 70, "ref_high": 85}
      ],
      "facts": [
        {"key": "кормление", "value": "раз в 10 дней", "entity": "B. hamorii"}
      ]
    }"""
    doc = parse_document(raw)
    assert doc.entities[0].kind == "паук"
    assert doc.entities[0].attributes == {"пол": "самка"}
    assert doc.measurements[0].entity == "Террариум №2"
    assert doc.measurements[0].category == "террариум"
    assert doc.facts[0].entity == "B. hamorii"
