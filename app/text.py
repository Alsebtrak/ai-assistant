"""Разбивка длинных ответов на сообщения Telegram."""

LIMIT = 3900


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Режет текст по абзацам/предложениям, не разрывая слово и не теряя конец."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        for separator in ("\n\n", "\n", ". ", "! ", "? "):
            position = window.rfind(separator)
            if position >= limit // 2:
                cut = position + len(separator.rstrip())
                break
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()

    if rest:
        parts.append(rest)
    return [part for part in parts if part]
