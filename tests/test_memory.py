from app.memory import chunk_text


def test_chunk_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n ") == []


def test_chunk_short_text_stays_whole():
    text = "Гемоглобин 148 г/л, всё в норме."
    assert chunk_text(text) == [text]


def test_chunk_long_text_covers_tail():
    text = " ".join(f"Показатель {i} в норме." for i in range(200))
    chunks = chunk_text(text, size=300, overlap=50)
    assert len(chunks) > 1
    assert all(len(chunk) <= 300 for chunk in chunks)
    assert "Показатель 199" in chunks[-1]


def test_chunk_does_not_loop_on_tiny_size():
    chunks = chunk_text("абвгдеёжзийклмнопрстуфхцчшщ", size=10, overlap=9)
    assert chunks
    assert all(chunks)
