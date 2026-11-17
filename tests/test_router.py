from app.router import classify, extract_url, is_safe_url


def test_classify_question():
    assert classify("Что ты знаешь про мой витамин D?") == "question"


def test_classify_link():
    assert classify("Посмотри https://kdl.ru/profile и сохрани") == "link"


def test_classify_remember():
    assert classify("Запомни: у меня аллергия на пенициллин") == "remember"
    assert classify("запиши, что лук 42 lb") == "remember"


def test_classify_query():
    assert classify("Покажи динамику АЛТ за год") == "chart"
    assert classify("Сколько раз я сдавал кровь?") == "query"


def test_classify_chart():
    assert classify("Построй график АЛТ") == "chart"
    assert classify("Покажи тренд витамина D") == "chart"


def test_classify_metric():
    assert classify("вес 80") == "metric"
    assert classify("давление 120/80") == "metric"
    assert classify("серия 108") == "metric"
    assert classify("Какой у меня вес?") != "metric"


def test_classify_remind():
    assert classify("напомни завтра в 10:00 поздравить Юлю") == "remind"
    assert classify("Не забудь про полив") == "remind"


def test_classify_search():
    assert classify("Найди сколько нужно на 1-й разряд по 3D стрельбе") == "search"
    assert classify("Узнай нормативы ГТО") == "search"


def test_extract_url_strips_trailing_punctuation():
    assert extract_url("смотри https://example.com/page).") == "https://example.com/page"
    assert extract_url("нет ссылки") is None


def test_is_safe_url_allows_https():
    assert is_safe_url("https://kdl.ru")
    assert is_safe_url("http://example.com/path")


def test_is_safe_url_blocks_local():
    assert not is_safe_url("http://localhost:8000")
    assert not is_safe_url("http://127.0.0.1")
    assert not is_safe_url("http://10.0.0.5/admin")
    assert not is_safe_url("http://192.168.1.1")
    assert not is_safe_url("ftp://example.com")
    assert not is_safe_url("http://metadata.google.internal")
