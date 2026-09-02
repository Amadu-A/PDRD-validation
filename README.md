<!-- README.md -->
# PDRD Validation — Drawing Validation AI

PDRD Validation — локальный сервис проверки проектной и рабочей документации по нормативной базе и Базе Опыта.

Пользователь загружает PDF, DXF/DWG или PDF вместе с CAD-файлом. Сервис извлекает текст и геометрию документа, формирует машинный контекст листа, анализирует его локальной VLM, подбирает релевантные нормативные фрагменты из Qdrant и формирует структурированный список замечаний.

Дополнительно пользователь может указать диапазон страниц Пояснительной записки. Эти страницы проходят отдельную проверку, индексируются во временную Project Context collection и используются как контекст проекта при анализе выбранных листов. Пояснительная записка не заменяет нормативную базу: нормативное замечание должно подтверждаться источником из постоянной нормативной коллекции.

Тяжёлые AI-задачи выполняются асинхронно через RabbitMQ/Celery с `concurrency=1`, поэтому параллельные пользовательские запросы не запускают несколько тяжёлых GPU-задач одновременно и не конкурируют за VRAM.

## Возможности

- PDF-only анализ;
- анализ нескольких выбранных страниц PDF;
- DXF-only анализ;
- DWG -> DXF normalization;
- PDF + CAD как два представления одного листа;
- контекст Пояснительной записки;
- временный semantic Project Context по ПЗ;
- нормативный RAG;
- поиск по Базе Опыта;
- локальная VLM через Ollama;
- асинхронная GPU-safe очередь;
- Transactional Outbox;
- n8n orchestration;
- status/result API;
- frontend без прямого доступа к n8n;
- автоматический cleanup временного Project Context;
- unit, integration и architecture tests.

## Основные технологии

| Слой | Технологии |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic |
| API / jobs | FastAPI, SQLAlchemy AsyncIO, Alembic |
| DB | PostgreSQL 16 |
| Очередь | RabbitMQ, Celery |
| Workflow orchestration | n8n |
| Vector DB | Qdrant |
| VLM | Ollama + `qwen3-vl:8b-instruct` |
| Embeddings | Ollama + `qwen3-embedding:4b` |
| PDF | PyMuPDF |
| CAD | ezdxf, LibreDWG |
| Frontend | HTML, CSS, JavaScript, nginx |
| Контейнеризация | Docker, Docker Compose |
| Тесты | pytest |
| Code style | Ruff |

# Архитектура

Проект разделён на четыре backend bounded context и отдельный frontend.

- **API Gateway** — публичный API, PostgreSQL job state, Transactional Outbox, RabbitMQ/Celery и хранение файлов анализа.
- **Document Service** — PDF/CAD extraction, render и нормализация DWG -> DXF.
- **Knowledge Service** — embeddings, Qdrant, нормативный RAG, Experience RAG и временный Project Context.
- **Analysis Service** — VLM-анализ, понимание листа, формирование поисковых запросов, нормативная проверка и финализация findings.
- **n8n** — orchestration между внутренними сервисами.
- **Frontend** — только Browser -> API Gateway. Браузер не обращается напрямую к n8n или внутренним микросервисам.

Shared infrastructure:

- Ollama;
- RabbitMQ;
- n8n.

Project-specific infrastructure:

- PostgreSQL;
- Qdrant;
- API Gateway;
- Celery worker;
- Outbox dispatcher;
- Document Service;
- Knowledge Service;
- Analysis Service;
- Frontend.

# Блок-схемы

## 1. Общий путь запроса

```mermaid
flowchart TD
    U[Пользователь] --> FE[Frontend nginx :8080]
    FE --> API[API Gateway :8200]

    API --> VALIDATE{Валидация multipart}
    VALIDATE -->|ошибка| ERR[4xx]
    VALIDATE -->|OK| FS[Analysis Artifact Store]
    VALIDATE -->|OK| PG[(PostgreSQL)]

    PG --> OUTBOX[Transactional Outbox]
    OUTBOX --> RMQ[RabbitMQ pdrd.analysis]
    RMQ --> WORKER[Celery worker concurrency=1]

    WORKER --> N8N[n8n V2 workflow]

    N8N --> DOC[Document Service]
    N8N --> ANALYSIS[Analysis Service]
    N8N --> KNOWLEDGE[Knowledge Service]

    ANALYSIS --> OLLAMA[Ollama VLM]
    KNOWLEDGE --> EMB[Ollama Embeddings]
    KNOWLEDGE --> QD[(Qdrant)]

    N8N --> WORKER
    WORKER --> FS
    WORKER --> PG

    FE --> POLL[GET /api/v1/analyses/job_id]
    POLL --> PG
    PG -->|pending / processing| POLL
    PG -->|completed| RESULT[GET result]
    RESULT --> REPORT[Отчёт в браузере]
```

## 2. Выбор режима анализа

```mermaid
flowchart TD
    INPUT[Загруженные файлы] --> MODE{Что загружено?}

    MODE -->|PDF| PDF[PDF-only]
    MODE -->|DXF / DWG| CAD[CAD-only]
    MODE -->|PDF + CAD| BOTH[PDF + CAD]

    PDF --> PAGES[Выбранные страницы PDF]
    CAD --> CADPREP[DXF parse / DWG -> DXF]
    BOTH --> ONEPAGE[Ровно одна PDF-страница + CAD]

    PAGES --> PZ{Использовать ПЗ?}
    ONEPAGE --> PZ2{Использовать ПЗ?}
    CADPREP --> NOPZ[ПЗ недоступна без PDF]

    PZ -->|нет| V2PDF[n8n PDF V2]
    PZ -->|да| PZPIPE[Project Context pipeline]

    PZ2 -->|нет| V2BOTH[n8n PDF+CAD V2]
    PZ2 -->|да| PZPIPE2[Project Context pipeline]

    CADPREP --> V2CAD[n8n CAD V2]
```

## 3. Анализ одного листа

```mermaid
flowchart TD
    DOC[Document extraction] --> FACTS[Page understanding]
    FACTS --> CTXQ[Project Context query]

    CTXQ --> PZSEARCH{ПЗ включена?}
    PZSEARCH -->|да| PZ[Semantic search по ПЗ]
    PZSEARCH -->|нет| EMPTY[Без Project Context]

    PZ --> AUG[Augment analysis context]
    EMPTY --> AUG

    FACTS --> NQ[Normative queries]
    AUG --> NQ

    NQ --> NORM[Normative search]
    NORM --> CHECK[VLM normative check]

    CHECK --> EXPQ[Experience queries]
    EXPQ --> EXP[Experience search]

    CHECK --> FINAL[Finding finalization]
    EXP --> FINAL

    FINAL --> RESULT[Структурированные findings]
```

## 4. Контекст Пояснительной записки

```mermaid
flowchart TD
    RANGE[Диапазон страниц ПЗ] --> TEXT[Text-only extraction]
    TEXT --> VALID[Analysis Service classification]

    VALID -->|не ПЗ| REJECT[Отклонить диапазон]
    VALID -->|ПЗ| CHUNK[Chunking]

    CHUNK --> EMB[Ollama embeddings]
    EMB --> TEMP[(Temporary Qdrant collection)]

    PAGE[Анализируемый лист] --> QUERY[Project Context query]
    QUERY --> TEMP
    TEMP --> SOURCES[Релевантные фрагменты ПЗ]

    SOURCES --> AUG[Контекст проекта]
    AUG --> NORM[Нормативная проверка]

    NORM --> CLEAN[Cleanup]
    CLEAN --> DELETE[Удалить temporary collection]
```

ПЗ используется только как описание проектных решений. Она не является нормативным доказательством.

## 5. GPU-safe очередь

```mermaid
flowchart TD
    U1[User 1] --> API[API Gateway]
    U2[User 2] --> API
    U3[User 3] --> API
    UN[User N] --> API

    API --> OUTBOX[Transactional Outbox]
    OUTBOX --> RMQ[RabbitMQ]

    RMQ --> J1[Job 1]
    RMQ --> J2[Job 2]
    RMQ --> J3[Job 3]
    RMQ --> JN[Job N]

    J1 --> W[Celery worker concurrency=1]
    J2 -. ждёт .-> RMQ
    J3 -. ждёт .-> RMQ
    JN -. ждёт .-> RMQ

    W --> N8N[n8n]
    N8N --> OLLAMA[Ollama]
    OLLAMA --> GPU[NVIDIA GPU]

    GPU --> DONE[Job completed]
    DONE --> NEXT[Следующая задача]
    NEXT --> RMQ
```

Backpressure находится в RabbitMQ: количество HTTP-запросов не равно количеству одновременно выполняющихся GPU inference.

## 6. Технологическая схема

```mermaid
flowchart LR
    subgraph CLIENT[Клиент]
        BR[Browser]
        FE[Frontend nginx]
    end

    subgraph APP[PDRD]
        GW[API Gateway]
        OB[Outbox dispatcher]
        CW[Celery worker]
        DS[Document Service]
        KS[Knowledge Service]
        AS[Analysis Service]
    end

    subgraph DATA[Project data]
        PG[(PostgreSQL)]
        QD[(Qdrant)]
        FS[(Analysis artifacts)]
    end

    subgraph SHARED[Shared infrastructure]
        RMQ[RabbitMQ]
        N8N[n8n]
        OL[Ollama]
    end

    BR --> FE --> GW
    GW --> PG
    GW --> FS
    PG --> OB
    OB --> RMQ
    RMQ --> CW
    CW --> N8N

    N8N --> DS
    N8N --> KS
    N8N --> AS

    KS --> QD
    KS --> OL
    AS --> OL

    CW --> PG
    CW --> FS
```

# Структура проекта

```text
PDRD-validation/
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── src/
│       ├── index.html
│       ├── css/
│       │   ├── variables.css
│       │   ├── global.css
│       │   ├── main.css
│       │   └── blocks/
│       │       ├── card.css
│       │       ├── form.css
│       │       ├── modal.css
│       │       └── report.css
│       └── js/
│           ├── app.js
│           ├── config.js
│           ├── components/
│           │   ├── modal.js
│           │   └── result.js
│           └── features/
│               └── analysis/
│                   ├── api.js
│                   ├── form.js
│                   ├── labels.js
│                   ├── polling.js
│                   └── report.js
│
├── services/
│   ├── api-gateway/
│   │   ├── alembic/
│   │   ├── src/pdrd_api_gateway/
│   │   │   ├── application/
│   │   │   ├── core/
│   │   │   ├── domain/
│   │   │   ├── infrastructure/
│   │   │   ├── transport/
│   │   │   └── main.py
│   │   └── tests/
│   │
│   ├── document-service/
│   │   ├── src/pdrd_document_service/
│   │   │   ├── application/
│   │   │   ├── core/
│   │   │   ├── domain/
│   │   │   ├── infrastructure/
│   │   │   ├── transport/
│   │   │   └── main.py
│   │   └── tests/
│   │
│   ├── knowledge-service/
│   │   ├── src/pdrd_knowledge_service/
│   │   │   ├── application/
│   │   │   ├── core/
│   │   │   ├── domain/
│   │   │   ├── infrastructure/
│   │   │   ├── transport/
│   │   │   └── main.py
│   │   └── tests/
│   │
│   └── analysis-service/
│       ├── src/pdrd_analysis_service/
│       │   ├── application/
│       │   ├── core/
│       │   ├── domain/
│       │   ├── infrastructure/
│       │   ├── transport/
│       │   └── main.py
│       └── tests/
│
├── n8n/
│   └── workflows/
│       ├── analysis-v2-pdf.json
│       ├── analysis-v2-cad.json
│       └── analysis-v2-pdf-cad.json
│
├── data/
│   └── knowledge/
│       ├── normative/
│       └── experience/
│
├── ops/
│   ├── Dockerfile.quality
│   └── check-quality.ps1
│
├── scripts/
│   ├── build_experience_cases.py
│   ├── check-stack.sh
│   ├── kb_common.py
│   ├── kb_search.py
│   └── kb_sync.py
│
├── tests/
│   └── architecture/
│
├── .env.example
├── compose.yaml
├── pyproject.toml
├── requirements-dev.txt
└── README.md
```

Backend-сервисы используют одинаковое направление зависимостей:

```text
Transport
    ↓
Application
    ↓
Domain

Infrastructure ──implements──> Application ports
```

`Domain` и `Application` не зависят от FastAPI, SQLAlchemy, Celery, Qdrant, Ollama или конкретных HTTP-клиентов.

# Конфигурация

Создать рабочий `.env`:

```bash
cp .env.example .env
```

Основные application defaults находятся в Pydantic Settings соответствующего сервиса. `.env` хранит deployment-specific параметры, которые действительно должны отличаться между окружениями: секреты, порты, имя Docker network и параметры project infrastructure. Это не дублирует все Pydantic defaults в Docker Compose.

## Обязательные секреты

В `.env` необходимо задать реальные значения минимум для:

```dotenv
PDRD_POSTGRES_PASSWORD=replace-me
PDRD_RABBITMQ_PASSWORD=replace-me
```

Секреты не коммитить.

Остальные application-настройки имеют project defaults в Pydantic Settings. Если конкретный параметр действительно потребуется менять между окружениями без изменения кода, он добавляется в Compose как явный deployment override, а не дублируется заранее.

# Запуск

## Требования

Project stack:

- Docker Engine;
- Docker Compose plugin.

Shared infrastructure должна быть уже запущена и доступна в Docker network `ai-shared`:

- RabbitMQ;
- n8n;
- Ollama.

В Ollama должны быть доступны:

```text
qwen3-vl:8b-instruct
qwen3-embedding:4b
```

## Запуск проекта

Из корня репозитория:

```bash
docker compose up -d --build --remove-orphans
```

Проверка всего stack:

```bash
bash scripts/check-stack.sh
```

Frontend:

```text
http://<server>:8080/
```

API Gateway Swagger:

```text
http://127.0.0.1:8200/docs
```

Остановка:

```bash
docker compose down
```

# Тестирование

## Быстрая проверка на Windows

```powershell
.\ops\check-quality.ps1 -Fix
```

Она выполняет Ruff, format check, pytest и architecture tests.

## Проверка в Docker

```bash
docker compose --profile test build quality-tests && \
docker compose --profile test run --rm quality-tests
```

## Runtime smoke

После запуска:

```bash
bash scripts/check-stack.sh
```

Скрипт проверяет:

- Docker / Docker Compose;
- project containers;
- Frontend;
- API Gateway;
- Document Service;
- Knowledge Service;
- Analysis Service;
- Qdrant;
- доступ API Gateway -> shared n8n.

# Публичный API

```text
POST /api/v1/analyses
GET  /api/v1/analyses/{job_id}
GET  /api/v1/analyses/{job_id}/result

GET  /health/live
GET  /health/ready
```

# Текущий статус

Stage 1 завершает перенос с legacy runtime на V2-архитектуру.

Подтверждены:

- PDF-only;
- multi-page PDF;
- CAD-only;
- PDF + CAD;
- PDF + ПЗ;
- PDF + CAD + ПЗ;
- валидация неправильного диапазона ПЗ;
- запрет CAD + ПЗ;
- Gateway -> Outbox -> RabbitMQ -> Celery -> n8n;
- temporary Qdrant Project Context cleanup;
- удаление legacy `pdf-service`.

Следующий этап — viewer/render/location:

- показ отрендеренного листа;
- выбор замечания;
- переход к соответствующей странице;
- normalized `bbox/location`;
- подсветка области замечания.
