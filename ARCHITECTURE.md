# Архитектура

Персональный ассистент: Telegram-бот + HTTP API, память документов и наблюдений,
все данные — в Yandex Cloud (РФ), LLM — OpenCode Zen.

## Компоненты

```mermaid
flowchart LR
    U[Пользователь] -->|фото, PDF, текст| BOT[Telegram-бот<br/>aiogram, polling]
    U -->|браузер| WEB[Web UI<br/>Next.js]

    BOT --> DB[(PostgreSQL 16)]
    BOT --> Q[(Redis / ARQ)]
    WEB --> API[FastAPI]
    API --> DB
    API --> Q

    Q --> W[Worker<br/>ARQ]
    W --> S3[(Object Storage<br/>Yandex)]
    W --> LLM[OpenCode Zen<br/>vision + chat]
    W --> DB

    API --> LLM
```

## Поток обработки документа

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant B as Бот
    participant S as Object Storage
    participant W as Worker
    participant L as Zen
    participant D as PostgreSQL

    U->>B: фото документа
    B->>S: сохранить файл
    B->>D: documents.status = pending
    B->>W: enqueue process_document
    W->>S: скачать файл
    W->>L: vision + JSON-схема
    L-->>W: title, text, entities, measurements, facts
    W->>D: extracted + status = processed
    W->>D: chunks (полнотекстовый индекс)
    W->>U: «Сохранить / Удалить»
    U->>B: Сохранить
    B->>D: measurements, entities, facts
```

## Модель данных

```mermaid
erDiagram
    USERS ||--o{ DOCUMENTS : "владеет"
    USERS ||--o{ ENTITIES : "владеет"
    DOCUMENTS ||--o{ CHUNKS : "содержит"
    DOCUMENTS ||--o{ MEASUREMENTS : "источник"
    DOCUMENTS ||--o{ FACTS : "источник"
    ENTITIES ||--o{ MEASUREMENTS : "о объекте"
    ENTITIES ||--o{ FACTS : "о объекте"

    USERS {
        bigint telegram_id PK
        string timezone
    }
    DOCUMENTS {
        uuid id PK
        string status
        text raw_text
        jsonb extracted
        date doc_date
    }
    CHUNKS {
        uuid id PK
        text content
        tsvector tsv
    }
    ENTITIES {
        uuid id PK
        string kind "паук, растение, террариум"
        string name
        jsonb attributes
    }
    MEASUREMENTS {
        uuid id PK
        string name
        string category
        numeric value
        string unit
        date measured_at
    }
    FACTS {
        uuid id PK
        string key
        string value
        timestamptz superseded_at
    }
```

## Деплой

```mermaid
flowchart TB
    subgraph YC["Yandex Cloud · ru-central1-a"]
        subgraph VM["ВМ Ubuntu 24.04 · 2 vCPU / 4 ГБ / 50 ГБ SSD"]
            NG[nginx :80/443] --> API2[api :8000]
            NG --> WEB2[web, статика]
            BOT2[bot] 
            WK[worker]
            PG[(postgres)]
            RD[(redis)]
        end
        BUCKET[(Object Storage<br/>документы, бэкапы)]
    end

    NET[Интернет] --> NG
    NET --> TG[Telegram Bot API]
    BOT2 --> TG
    BOT2 --> PG
    WK --> PG
    WK --> BUCKET
    WK --> ZEN[OpenCode Zen]
    API2 --> ZEN
```

## Границы доверия

- Наружу открыты только `443` (nginx) и `22` (SSH с ключом).
- API закрыт `WEB_TOKEN`, публичной регистрации нет.
- Бот отвечает только Telegram ID из `TELEGRAM_ALLOWED_IDS`.
- Фото и PDF лежат в приватном бакете; доступ — по ключу сервисного аккаунта.
- В Zen уходят только текст запроса и изображение документа; ключи и БД наружу не уходят.

## Telegram из России

`api.telegram.org` с российских сетей недоступен (проверено: IPv4 — таймаут, IPv6 — отказ).
Порядок действий:

1. На ВМ в Yandex Cloud проверить `curl -I https://api.telegram.org` — у облака свой аплинк, может работать.
2. Если не работает: зарубежный VPS (~300–400 ₽/мес, 1 vCPU / 1 ГБ) с HTTP-прокси,
   разрешённым только на `api.telegram.org`; в `.env` задать `TELEGRAM_PROXY_URL`.
3. Прокси видит только домен и порт — содержимое защищено TLS.

## Этапы

| Фаза | Что | Статус |
|---|---|---|
| 0 | ВМ, S3, docker compose, бэкапы | ждёт разблокировки биллинга |
| 1 | документы → OCR → подтверждение → RAG | код готов |
| 2 | графики метрик, карточки объектов | не начато |
| 3 | календарь, почта, напоминания по уходу | не начато |
| 4 | Web UI, голос, агентские сценарии | не начато |
