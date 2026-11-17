# AI Assistant

Персональный ассистент: память документов и наблюдений, метрики, объекты (питомцы,
растения, террариумы, инвентарь), вопросы по базе. Telegram-бот + HTTP API.
Хранение — Yandex Cloud (ВМ + Object Storage).

## Запуск

```bash
cp .env.example .env   # заполнить токены
docker compose up -d --build
```

- `api` — FastAPI на порту 8000
- `bot` — Telegram-бот (polling, домен не нужен)
- `worker` — обработка документов через ARQ

Тесты: `pip install -e ".[dev]" && pytest`

## Как работает

1. Фото или PDF → Object Storage → задача `process_document`.
2. Воркер распознаёт документ через vision-модель (PDF — сначала текстовый слой).
3. Бот присылает распознанное и кнопки «Сохранить / Удалить».
4. После подтверждения метрики пишутся в `measurements`, объекты — в `entities`,
   устойчивые факты — в `facts`.
5. Вопрос текстом → полнотекстовый поиск по чанкам + профиль и объекты → ответ со ссылками.

## API

Все методы, кроме `/health`, требуют `Authorization: Bearer $WEB_TOKEN`.

- `GET /documents`
- `POST /documents` (multipart, до 20 МБ)
- `GET /measurements?name=&category=`
- `GET /entities`
- `POST /ask {"question": "..."}`

## Импорт анализов КДЛ

КДЛ отдаёт результаты только авторизованной сессии (телефон + дата рождения +
капча + SMS). Поэтому куки экспортируются из браузера один раз:

1. Войти в `kdl.ru`, открыть DevTools → Application → Cookies → `https://kdl.ru`.
2. Сохранить `PHPSESSID` в `.secrets/kdl_cookies.json`:

```json
{"PHPSESSID": "..."}
```

3. Запустить импорт:

```bash
python -m app.kdl sync --cookies .secrets/kdl_cookies.json
```

Импорт идемпотентен: повторный запуск обновляет значения, а не плодит дубликаты
(`source=kdl`, `external_id=<заказ>:<код теста>`). Нечисловые результаты
(«не обнаружено», «<6.0») пропускаются, чтобы не искажать графики.

