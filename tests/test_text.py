from app.text import split_message


def test_split_short_text_unchanged():
    assert split_message("Короткий ответ") == ["Короткий ответ"]


def test_split_empty():
    assert split_message("") == []
    assert split_message("   ") == []


def test_split_long_text_keeps_tail():
    text = " ".join(f"Предложение номер {i}." for i in range(300))
    parts = split_message(text, limit=200)
    assert len(parts) > 1
    assert all(len(part) <= 200 for part in parts)
    assert "номер 299" in parts[-1]


def test_split_prefers_paragraph_boundary():
    text = "А" * 100 + "\n\n" + "Б" * 100
    parts = split_message(text, limit=110)
    assert parts[0] == "А" * 100
    assert parts[1] == "Б" * 100
