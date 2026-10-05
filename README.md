<!-- README.md -->

# PDRD Validation — проверка чертежей с помощью ИИ

PDRD Validation — локальный сервис проверки проектной и рабочей документации по нормативной базе, техническому заданию, пользовательским пакетам документов, контексту проекта и Базе Опыта.

Пользователь загружает PDF, DXF/DWG или PDF вместе с соответствующим CAD-файлом, при необходимости прикладывает Техническое задание и выбирает нормативный раздел/пользовательские документы. Система извлекает текст и геометрию, формирует машинный контекст листа, выполняет semantic retrieval по источникам разных типов, запускает локальный VLM-анализ и возвращает структурированные замечания с разделённой доказательной базой `N/T/U/E`.

Тяжёлые GPU-задачи выполняются с общим cross-process GPU lease. Analysis VLM и unified embedding runtime не должны одновременно загружать несовместимые тяжёлые модели в одну GPU без предварительной проверки VRAM.

## Возможности

- PDF-only и multi-page PDF;
- DXF-only и DWG -> DXF normalization;
- PDF + CAD как два представления одного листа;
- Техническое задание как отдельный project/customer source;
- multimodal indexing страниц ТЗ;
- T-guided нормативный retrieval;
- контекст Пояснительной записки;
- временный semantic Project Context;
- managed нормативные разделы и вложенные папки;
- пользовательские пакеты документов внутри выбранного раздела;
- PDF/DOC/DOCX upload;
- Word -> PDF preview через LibreOffice;
- durable индексация через Transactional Outbox;
- scoped normative RAG по immutable snapshot;
- scoped retrieval выбранных пользовательских документов;
- отдельный system prompt нормативного раздела;
- transient working prompt;
- кликабельные нормативные и T sources;
- Human Review в браузере: Wise/Bad/Edited/Gold, выделение Gold на листе и синхронизация с текстовым списком;
- серверная доменная модель Human Review, утверждение ревизий и отбор подтверждённых областей для будущей Базы Опыта;
- итоговый PDF актуального утверждённого Human Review: принятые замечания, проверенные области VLM и жёлтый Gold на листе и в текстовом списке;
- База Опыта (достоверное сохранение, HTTP и E indexing вводятся поэтапно; поиск E отключён до завершения контура);
- единая embedding model для N/T/U/E/PZ;
- blue/green переиндексация Qdrant при смене embedding identity;
- cross-process GPU lease и RAM/VRAM admission;
- n8n orchestration;
- frontend только через API Gateway;
- cleanup временного Project Context;
- unit, integration, architecture и GPU runtime tests.

# Семантика источников N / T / U / E

Источники не объединяются в одну семантическую роль. Тип источника определяет, что именно он может доказывать.

| Префикс | Тип | Роль |
|---|---|---|
| `N1`, `N2`, ... | Normative | нормативная база: ГОСТ, СП, ПУЭ и другие нормативные требования |
| `T1`, `T2`, ... | Technical Assignment | требования ТЗ, заказчика и проекта |
| `U1`, `U2`, ... | User Package | пользовательские документы проекта/заказчика |
| `E1`, `E2`, ... | Experience | База Опыта, используемая при finalization/recommendation |

Главные правила:

- только `N` может подтверждать утверждение о нарушении нормативного документа;
- `T` является самостоятельным project/customer requirement и может подтверждать `customer_requirements`;
- `T` может содержать ссылки на нормативы и направлять targeted N retrieval, но не превращается в норматив;
- `U` является самостоятельным пользовательским/project source, но не нормативным доказательством;
- `E` является опытом, а не нормативным доказательством. Контракт retrieval существует, но `KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=false` до trusted ingestion и оценки качества;
- finding без N/T/U не удаляется автоматически: инженерное/визуальное замечание может остаться `needs_review`;
- `normative_control` без валидного `N` не должен сохраняться только на основании `T` или `U`.

Итоговые typed source arrays:

```text
basis_sources                       = N
technical_assignment_basis_sources  = T
user_package_basis_sources          = U
experience_sources                  = E
```

# Технологии

| Слой | Технологии |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic |
| Persistence | PostgreSQL 16, SQLAlchemy AsyncIO, Alembic |
| Очередь | RabbitMQ, Celery |
| Orchestration | n8n |
| Vector DB | Qdrant |
| VLM | shared vLLM OpenAI-compatible endpoint `http://shared-vlm:8000/v1`, logical model `shared-vlm` |
| Embeddings | shared vLLM endpoint `http://shared-embedding:8000/v1`, logical model `shared-embedding` |
| Embedding dimension | `4096` |
| PDF | PyMuPDF |
| Word | LibreOffice headless |
| CAD | ezdxf, LibreDWG |
| Frontend | HTML, CSS, JavaScript, nginx |
| Контейнеризация | Docker, Docker Compose |
| Тесты / style | pytest, Ruff |

# Архитектура

Bounded contexts:

- **API Gateway** — публичный API, job state, immutable analysis snapshot, Outbox, Celery, analysis artifacts и public content proxy для managed sources.
- **Document Service** — PDF/CAD extraction, render, DWG -> DXF.
- **Knowledge Service** — managed catalog, ТЗ lifecycle, PostgreSQL metadata, Qdrant, N/T/U/E retrieval и Project Context.
- **Experience Service** — отдельный bounded context: Human Review, решения и аудит, проверенные области, закрытый HTTP API, PostgreSQL-каталог и собственное хранилище PNG. Оригинал для crop получает через Gateway, вырезание выполняет Document Service. Рабочее развёртывание каждой новой версии проверяется отдельно.
- **Shared Embedding Runtime** — общий external endpoint `shared-embedding`; локальный каталог `multimodal-embedding-service` сохраняется в репозитории как legacy/test code, но не поднимает второй runtime в Compose.
- **Analysis Service** — VLM page understanding, requirement check, N/T/U policy и finalization.
- **n8n** — orchestration внутренних вызовов.
- **Frontend** — Browser -> API Gateway; прямого доступа к n8n и внутренним сервисам нет.

Shared infrastructure (самостоятельный lifecycle, сеть `ai-shared`):

- `shared-vlm` (vLLM);
- `shared-embedding` (vLLM);
- RabbitMQ;
- n8n.

Project infrastructure:

- PostgreSQL (`analysis_jobs`/`knowledge` и отдельная схема Experience после миграции);
- Qdrant и проектные application services;
- analysis/document/knowledge volumes;
- Experience Service запускается профилем `review`, без копирования shared GPU runtimes.

Направление зависимостей backend:

```text
Transport
    ↓
Application
    ↓
Domain

Infrastructure ──implements──> Application ports
```

`Domain` и `Application` не зависят от FastAPI, SQLAlchemy, Celery, Qdrant, Ollama и HTTP adapters.

# Блок-схемы

## 1. Общий путь анализа

```mermaid
flowchart TD
    U["Пользователь"] --> FE["Frontend :8080"]
    FE --> GW["API Gateway :8200"]
    GW --> FS["Analysis Artifact Store"]
    GW --> KS["Knowledge Service"]
    KS --> RESOLVE["Immutable N/T/U selection"]
    RESOLVE --> GW
    GW --> PG[("PostgreSQL")]
    PG --> O["API Gateway Outbox"]
    O --> RMQ["Shared RabbitMQ"]
    RMQ --> W["Project Celery worker"]
    W --> N8N["Shared n8n: stage-scoped V2 workflows"]
    N8N --> DS["Document Service"]
    N8N --> KS2["Knowledge Service"]
    N8N --> AS["Analysis Service"]
    KS2 --> QD[("Project Qdrant")]
    KS2 --> EMB["Shared vLLM embedding endpoint"]
    AS --> VLM["Shared vLLM vision endpoint"]
    N8N --> W
    W --> PG
    W --> FS
    FE --> POLL["Status / result / visualization polling"]
    POLL --> GW
    GW --> DS2["Document Service: automatic and reviewed PDF renderer"]
    FE -->|Human Review: основной UI 8080| GW
    GW --> EXP["Experience Service: профиль review"]
    EXP --> RP[("PostgreSQL: схема experience")]
    EXP -->|approved Review manifest| GW
```

Точки и модели shared-inference задаются logical endpoints/переменными окружения, а не физическими ID модели в коде проекта. Experience формирует утверждённую проекцию Review, Gateway передаёт её PDF-рендереру Document Service. Review сохраняется через Gateway на основном фронте 8080. Утверждение и скачивание PDF автоматически сохраняют подходящие примеры Experience. Действующий `annotated-pdf` остаётся **автоматической исходной версией**.

## 2. Managed N/U catalog и индексация

```mermaid
flowchart TD
    U["Пользователь"] --> FE["Frontend"]
    FE --> GW["API Gateway /api/v1/normative"]
    GW --> KS["Knowledge Service internal API"]

    KS --> PG[("knowledge schema")]
    KS --> STORE[("normative_documents volume")]

    PG --> OUTBOX["Knowledge Outbox"]
    OUTBOX --> RMQ["RabbitMQ pdrd.knowledge.indexing"]
    RMQ --> IDX["knowledge-indexer concurrency=1"]

    IDX --> STORE
    IDX --> TYPE{"Формат"}
    TYPE -->|PDF| PDF["PDF"]
    TYPE -->|DOC / DOCX| LO["LibreOffice -> PDF preview"]
    LO --> PDF

    PDF --> TEXT["Page extraction"]
    TEXT --> CHUNK["Chunking"]
    CHUNK --> EMB["Unified text embedding\nQwen3-VL-Embedding-8B"]
    EMB --> MANAGED[("dva_catalog_active")]

    IDX --> PG
```

`N` и `U` используют один physical vector space, но semantic role определяется PostgreSQL `catalog_area` до vector search.

## 3. Техническое задание

```mermaid
flowchart TD
    TFILE["Техническое задание PDF/DOC/DOCX"] --> GW["API Gateway"]
    GW --> ART["Immutable analysis artifact"]
    GW --> KS["Knowledge Service"]

    KS --> TPG[("technical_assignments metadata")]
    KS --> TSTORE[("technical_assignment_documents volume")]
    TPG --> TO["T Outbox"]
    TO --> RMQ["RabbitMQ pdrd.knowledge.technical-assignment"]
    RMQ --> TW["technical-assignment-indexer\nconcurrency=1 / prefetch=1"]

    TW --> TSTORE
    TW --> PAGE["Page text + rendered image"]
    PAGE --> EMB["Qwen3-VL-Embedding-8B"]
    EMB --> TQ[("dva_technical_assignment_active")]
    TW --> READY["index_status=ready"]

    READY --> ANALYSIS["Analysis allowed to enter n8n"]
```

ТЗ индексируется до запуска n8n. Analysis job не начинает orchestration, пока связанный T-index не перешёл в `READY`.

## 4. Анализ одного листа с N/T/U

```mermaid
flowchart TD
    DOC["Document extraction"] --> FACTS["Page understanding (stage-scoped)"]
    FACTS --> PZQ["Project Context query"]
    PZQ --> PZ{"ПЗ включена?"}
    PZ -->|да| PZS["Temporary Project Context"]
    PZ -->|нет| EMPTY["Без ПЗ"]
    PZS --> Q["Grouped/typed retrieval queries"]
    EMPTY --> Q
    FACTS --> Q
    Q --> REQ{"ТЗ подключено?"}
    REQ -->|нет| NS["General N retrieval"]
    REQ -->|да| TS["T multimodal retrieval"]
    TS --> TN["T-guided N retrieval"]
    TN --> MERGE["Typed requirement context"]
    NS --> MERGE
    TS --> MERGE
    Q --> US["Search User Packages"]
    MERGE --> NCTX["N1, N2, ..."]
    MERGE --> TCTX["T1, T2, ..."]
    US --> UCTX["U1, U2, ..."]
    NCTX --> CHECK["VLM requirements check"]
    TCTX --> CHECK
    UCTX --> CHECK
    CHECK --> FQ["Finding-local normative queries"]
    FQ --> FN["Finding-local N retrieval"]
    FN --> FINALCTX["Normative enrichment"]
    CHECK --> EQ["Optional Experience queries"]
    FINALCTX --> EQ
    EQ --> ENABLED{"Experience enabled?"}
    ENABLED -->|нет: текущий default| EMPTY_E["No E evidence"]
    ENABLED -->|да: после trusted ingestion| ES["Trusted E retrieval"]
    EMPTY_E --> FINAL["Finalization"]
    ES --> FINAL
    FINAL --> RESULT["Final findings: N/T/U/E separately"]
```

`E` в `.env.example` отключён: наличие в workflow шага Experience не означает работающую доверенную базу. Промежуточные гипотезы не удаляются, но не становятся подтверждёнными замечаниями без дополнительной проверки.

## 5. GPU coordination

```mermaid
flowchart TD
    Q["Project stage requests"] --> SERVICE{"Workload"}
    SERVICE -->|Vision| V["shared-vlm :8000/v1"]
    SERVICE -->|Embedding| E["shared-embedding :8000/v1"]
    V --> CONFIG["Shared vLLM GPU placement and model residency"]
    E --> CONFIG
    SERVICE -->|Project-specific GPU-heavy stage if used| LEASE["Cross-process GPU lease"]
    LEASE --> WAIT["Resource admission and bounded wait"]
    WAIT --> WORK["Stage execution with exclusive lease"]
    WORK --> RELEASE["Release lease"]
```

Shared runtimes управляют собственным размещением GPU и residency; приложение использует stable logical endpoints. Проектный lock `/var/lock/pdrd-gpu/gpu.lock` остаётся контрактом только для операций, действительно использующих project-side GPU lease; **его нельзя представлять как lock, который гарантированно управляет shared vLLM из другого стека**. Конкретные GPU/TP/DP/model IDs берутся из конфигурации shared infrastructure.

## 6. PostgreSQL — таблицы и связи

Один project PostgreSQL instance; сервисы владеют независимыми bounded contexts. У Experience собственная схема `experience` и отдельная таблица Alembic `experience.alembic_version_experience`, не изменяющая цепочки миграций Gateway/Knowledge. Миграцию Experience следует выполнять только после отдельной проверки подключения и backup; **наличие миграции в Git не означает, что она применена на рабочем сервере**.

```mermaid
erDiagram
    ANALYSIS_JOBS ||--o{ OUTBOX_MESSAGES : publishes
    ANALYSIS_JOBS ||..o| EXPERIENCE_REVIEW_SESSIONS : "logical job_id, no cross-schema FK"
    EXPERIENCE_REVIEW_SESSIONS ||--o{ EXPERIENCE_REVIEW_EVENTS : audits
    EXPERIENCE_REVIEW_SESSIONS ||--o{ CONFIRMED_AREAS : "versioned explicit confirmation"
    EXPERIENCE_REVIEW_SESSIONS ||..o{ EXPERIENCE_CANDIDATES : "planned verified selection"
    NORMATIVE_SECTIONS ||--o{ NORMATIVE_CATEGORIES : contains
    NORMATIVE_SECTIONS ||--o{ NORMATIVE_DOCUMENTS : contains
    NORMATIVE_CATEGORIES ||--o{ NORMATIVE_CATEGORIES : parent
    NORMATIVE_CATEGORIES ||--o{ NORMATIVE_DOCUMENTS : groups
    NORMATIVE_DOCUMENTS ||--o{ NORMATIVE_OUTBOX_MESSAGES : indexes
    NORMATIVE_SECTIONS ||--o{ TECHNICAL_ASSIGNMENTS : scopes
    TECHNICAL_ASSIGNMENTS ||--o{ TECHNICAL_ASSIGNMENT_OUTBOX_MESSAGES : indexes
```

### API Gateway

`analysis_jobs` — lifecycle задания; `normative_snapshot` является immutable JSONB. Изменение frontend после запуска не модифицирует существующий job.

Snapshot содержит независимо:

```json
{
  "section_id": "<uuid>",
  "document_ids": ["<normative-uuid>"],
  "user_package_document_ids": ["<user-package-uuid>"],
  "system_prompt": "<exact resolved prompt>",
  "technical_assignment": {
    "technical_assignment_id": "<uuid>",
    "analysis_document_id": "<uuid>",
    "source_file": "ТЗ.pdf"
  }
}
```

`technical_assignment` отсутствует без ТЗ. `outbox_messages` — transactional outbox анализа, публикуемый только после SQL commit.

### Knowledge Service

- `knowledge.normative_sections` — разделы и system prompt;
- `knowledge.normative_categories` — дерево N/U (`parent_id`, `catalog_area`);
- `knowledge.normative_documents` — metadata, storage key, durable index statuses;
- `knowledge.normative_outbox_messages` — события N/U indexing;
- `knowledge.technical_assignments` — metadata и lifecycle T;
- `knowledge.technical_assignment_outbox_messages` — отдельная durable T queue;
- `alembic_version_knowledge` — собственная цепочка миграций.

### Experience Service

**Контракт текущего этапа:** `experience.review_sessions` хранит последний JSONB-снимок одного `job_id` с `revision` и `approved_revision`; `experience.review_events` хранит неизменяемую последовательность действий `(job_id, session_revision)` с before/after и автором. Каждая запись в транзакции проходит optimistic CAS по `expected_revision`. В `ReviewSession` хранятся также замечания без координат — для полного аудита и итогового **текстового** PDF. Отбор обучающих примеров — отдельная операция, **не** копия всей таблицы Review. Предложенные визуализацией области VLM переносятся как `proposed_regions` с источником и уверенностью; `issue_box` VLM не заполняется автоматически, подтверждения хранятся отдельно. Старые JSONB-снимки без `proposed_regions` продолжают читаться.

**Этап 5:** серверный Review API соединяет Gateway, доверенный источник завершённого анализа, Experience Service и frontend. Восстанавливаются решения, исправления и геометрия VLM/Gold. `display_regions` хранит операционные правки отдельно от подтверждений областей. Предоставленные пользователем логи коммита `c1738fe` подтверждают Windows/Linux quality gate и развёртывание Experience на Linux с миграцией `20260928_0002`. Основной frontend 8080 подключён к серверному Review; дополнительный 8081 остаётся на loopback. Конфигурация и ограничения описаны в [docs/review-api.md](docs/review-api.md).

**Этап 6:** реализованы явное подтверждение/отзыв областей VLM и Reviewed PDF из актуальной утверждённой редакции. Experience готовит проекцию, Gateway проверяет доступ, источник и кеш, Document Service рисует PDF. Полный текст всех accepted включается в приложение; аннотации на листах требуют проверенной области, Gold сохраняет жёлтое оформление и заданную карточку. Rejected исключены. Логи коммита `e0e9762` подтверждают Windows/Linux — 965 passed и изолированный PostgreSQL — 14 passed. Итоговый PDF доступен на основном frontend 8080 после рассмотрения всех замечаний и сохранения решений.

**Этап 7:** реализованы постоянные примеры с PNG, серверные фильтры, история правок, деактивация и ZIP-экспорт. Утверждение Review запускает сохранение проверенных примеров; отдельная кнопка позволяет повторить его после сбоя. Edited + Rejected требует причины и выбора отрицательной формулировки. Контракт и запуск: [docs/experience-catalog.md](docs/experience-catalog.md). Обновление этого этапа на Linux проверяется отдельно.

**Следующие этапы:** контролируемая индексация E и отдельный эксперимент обучения. План 0–9: [docs/experience-roadmap.md](docs/experience-roadmap.md).

## 7. Qdrant — stable aliases и physical collections

```mermaid
flowchart LR
    KS["Knowledge Service"] --> CA["dva_catalog_active"]
    KS --> TA["dva_technical_assignment_active"]
    KS --> EA["dva_experience_active"]
    KS --> P["pdrd_project_context_<context_id>"]

    CA --> CP[("dva_catalog_<fingerprint>")]
    TA --> TP[("dva_technical_assignment_<fingerprint>")]
    EA --> EP[("dva_experience_<fingerprint>")]

    CP --> NU["N + U chunks"]
    TP --> T["T page multimodal points"]
    EP --> E["Experience points"]

    P --> PP["Временная ПЗ"]
    PP --> CLEAN["Cleanup после анализа"]
```

Stable aliases:

```text
dva_catalog_active
dva_technical_assignment_active
dva_experience_active
```

Physical collection name определяется fingerprint:

```text
fingerprint = sha256(model | dimension | schema_version)[:16]
```

Для текущей `.env.example` логической identity:

```text
PDRD_EMBEDDING_MODEL=shared-embedding
PDRD_EMBEDDING_DIMENSION=4096
PDRD_EMBEDDING_SCHEMA_VERSION=2
```

Физический checkpoint и hardware layout задаются shared-infrastructure; изменение логической identity или схемы векторов требует контролируемой миграции.

### Managed N/U payload

Для PDF point соответствует chunk физической страницы. Для DOC/DOCX сначала создаётся PDF-preview.

```json
{
  "document_id": "<uuid>",
  "section_id": "<uuid>",
  "category_id": "<uuid-or-null>",
  "source_sha256": "<sha256>",
  "source_file": "<original filename>",
  "page": 17,
  "chunk_index": 2,
  "text": "<fragment>"
}
```

`catalog_area` намеренно остаётся source of truth в PostgreSQL.

Перед vector search Knowledge Service:

1. получает IDs из immutable snapshot;
2. проверяет existence;
3. проверяет `section_id`;
4. проверяет `catalog_area`;
5. проверяет `index_status=ready`;
6. строит exact Qdrant filter по `document_id`.

### T payload

Один T point соответствует странице и содержит multimodal representation:

```json
{
  "source_type": "technical_assignment",
  "representation": "page_multimodal",
  "technical_assignment_id": "<uuid>",
  "analysis_document_id": "<uuid>",
  "section_id": "<uuid>",
  "source_file": "ТЗ.pdf",
  "source_sha256": "<sha256>",
  "page": 4,
  "pixel_width": 1240,
  "pixel_height": 1754,
  "normative_refs": [
    "СП 256.1325800.2016"
  ],
  "text": "<page text>"
}
```

### Experience payload

Текущий domain `ExperienceCandidate` описывает **подготовку записи**, но действующая E-коллекция пока не пополняется из Human Review. Планируемый payload после сохранения изображения и проверки прав:

```json
{
  "job_id": "<uuid>",
  "document_id": "<uuid>",
  "source_sha256": "<sha256>",
  "finding_id": "<stable source finding id>",
  "approved_revision": 4,
  "origin": "vlm",
  "tag": "edited",
  "decision": "rejected",
  "learning_use": "needs_adjudication",
  "page_number": 22,
  "issue_regions": [{"x_min": 100, "y_min": 150, "x_max": 300, "y_max": 380}],
  "original_text": "<original VLM interpretation>",
  "text": "<engineer correction>",
  "normative_basis": "<verified or explicitly unverified reference>",
  "crop_asset_id": "<object storage reference>",
  "embedding_text": "<selected trusted search text>"
}
```

`tag=edited` и `decision=rejected` показываются как **Edited · Bad**; они не становятся автоматически положительными/отрицательными обучающими примерами (`needs_adjudication`). `E` не нормативный basis. Pending и записи без подтверждённой области не индексируются. `KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=false` остаётся до завершения индексации и слепой оценки.

### Project Context

Временная collection Пояснительной записки конкретного analysis context:

```json
{
  "page": 12,
  "chunk_index": 1,
  "text": "<fragment>"
}
```

ПЗ — контекст проекта, а не нормативное доказательство.

## 8. Blue/green embedding migration

При смене:

```text
PDRD_EMBEDDING_MODEL
PDRD_EMBEDDING_DIMENSION
PDRD_EMBEDDING_SCHEMA_VERSION
```

меняется embedding fingerprint.

```mermaid
flowchart TD
    START["Stack startup"] --> MIG["knowledge-embedding-migrator"]
    MIG --> ID["Compute current fingerprint"]
    ID --> SAME{"Aliases already point\nto current targets?"}

    SAME -->|да| NOOP["Fast no-op"]
    SAME -->|нет| CREATE["Create new physical collections"]

    CREATE --> NU["Reindex persisted N/U files"]
    CREATE --> T["Reindex persisted READY T files"]
    CREATE --> E["Re-embed persisted trusted Experience only if available"]

    NU --> CHECK["All rebuilds succeeded"]
    T --> CHECK
    E --> CHECK

    CHECK --> SWITCH["Atomic alias switch"]
    SWITCH --> CLEAN["Delete obsolete managed/legacy collections"]
```

Source documents не восстанавливаются из старых vectors:

- N/U перечитываются из `normative_documents`;
- T перечитывается из `technical_assignment_documents`;
- metadata читаются из PostgreSQL;
- E после включения доверенного контура переэмбеддится только из сохранённых проверенных payload (в текущем режиме E выключен);
- временные Project Context создаются заново в конкретном analysis run.

Если rebuild падает до cutover:

- текущие aliases не переключаются;
- старый рабочий vector space остаётся доступным;
- незавершённые новые targets очищаются.

## 9. Физическое хранение

```mermaid
flowchart TD
    PG[("Project PostgreSQL")] --> PGV["postgres_data"]
    QD[("Project Qdrant")] --> QDV["qdrant_data"]
    GW["API Gateway / worker"] --> AV["analysis_artifacts"]
    KS["Knowledge Service / indexer"] --> NV["normative_documents"]
    TIDX["T indexer"] --> TV["technical_assignment_documents"]
    EX["Experience metadata and review"] --> PGV
    EX --> CROP["Immutable SHA-256 PNG storage: experience_crops volume"]
    PGV --> PGP["/var/lib/postgresql/data"]
    QDV --> QDP["/qdrant/storage"]
    AV --> AP["/data/analyses"]
    NV --> NP["/data/normative"]
    TV --> TP["/data/technical-assignments"]
    VLM["shared-vlm/shared-embedding"] --> SHARED["Shared runtime storage: separate stack"]
```

| Данные | Docker volume / owner | Путь |
|---|---|---|
| PostgreSQL (включая будущую `experience` schema) | `postgres_data` | `/var/lib/postgresql/data` |
| Qdrant | `qdrant_data` | `/qdrant/storage` |
| Analysis artifacts | `analysis_artifacts` | `/data/analyses` |
| Managed N/U files | `normative_documents` | `/data/normative` |
| T files | `technical_assignment_documents` | `/data/technical-assignments` |
| Shared vLLM/embedding weights | `shared-infrastructure` | Не является volume проекта |
| Experience image crops | собственный volume `experience_crops` | `/data/experience/crops/<sha256-prefix>/<sha256>.png` |

Не использовать `docker compose down -v` в обычном деплое: он уничтожает постоянные данные.

## 10. Группировка и локализация замечаний

```mermaid
flowchart TD
    RAW["Completed analysis findings"] --> CLASS{"status=hypothesis?"}
    CLASS -->|да| H["Unverified hypothesis disclosure: no bbox/callout"]
    CLASS -->|нет| LOC["Server-owned page localization"]
    LOC --> HAS{"regions found?"}
    HAS -->|нет| TEXT["Textual review item without invented bbox"]
    HAS -->|да| GROUP["Nearby visual groups: page + object_ref/geometry"]
    GROUP --> SHARED["Shared callout frame for nearby same-object findings"]
    SHARED --> IDS["Each member retains own finding_id, controls and decision"]
    TEXT --> REVIEW["Human Review"]
    IDS --> REVIEW
```

**Реализовано во frontend:** пространственная группировка влияет только на отображение; каждый `finding_id` остаётся независимым в review/DB. Hypothesis не создаёт ложную рамку. Группировка не объединяет доказательства, решения или оригинальные тексты в одну запись. Gold всегда имеет отдельный собственный `finding_id` и исходно выбранные пользователем две области.

**Действия с Gold и VLM:** стороны и углы рамки/карточки растягиваются мышью, Escape отменяет жест. Изменение координат сбрасывает решение в `pending`. Красный крестик отклоняет запись и скрывает её рамку, линию и текст; кнопка над листом отменяет последнее действие. Отдельных кнопок изменения областей и удаления нет. В карточке нет скролла; полный ответ доступен в тултипе. История кнопки Undo живёт до открытия другого отчёта. На закрытом фронте решения, тексты и геометрия сохраняются сервером и восстанавливаются после перезагрузки. Undo создания Gold там записывает rejected, сохраняя аудит; в локальном режиме отменяет добавление. Автоматический PDF эти решения не применяет.

## 11. Полный бизнес-процесс Experience Service

```mermaid
flowchart TD
    A["Completed analysis: immutable source findings"] --> V["Lazy visualization and source PDF"]
    V --> GW["API Gateway: закрытый Review API"]
    GW --> OPEN["Experience OpenReview: original VLM findings pending"]
    OPEN --> UI["Human Review UI: Wise / Bad / Edited / Gold"]
    UI --> EDIT["Edit VLM: original + corrected version; decision resets"]
    UI --> MANUAL["Add Gold: same-page issue and callout rectangles"]
    UI --> DECIDE["Independent decision for every finding"]
    EDIT --> DECIDE
    MANUAL --> DECIDE
    DECIDE --> CAS["ReviewRepository: revision CAS + append-only events"]
    CAS --> ALL{"All decisions recorded?"}
    ALL -->|нет| UI
    ALL -->|да| APPROVE["Explicit approval of current review revision"]
    APPROVE --> PDF["Experience manifest → Gateway → Document Service: Reviewed PDF"]
    PDF --> PAGE["Accepted with location: annotation on original page"]
    PDF --> TEXT["All accepted: textual appended list, including unlocated"]
    APPROVE --> PICK["SelectExperience: separate trusted selection"]
    PICK --> STORE["PostgreSQL catalog + original PDF crops via Document Service"]
    STORE --> INDEX["Knowledge: закрытый feed и индексатор текста + crop"]
    INDEX --> QD[("Коллекция выбранной версии по identity embedding")]
```

**Статусы внедрения:** Human Review UI, `ReviewSession`, PostgreSQL/Alembic,
аудит областей, серверный Review API, Reviewed PDF и постоянный каталог реализованы.
Зелёная галочка атомарно принимает замечание и текущую сохранённую область;
отдельной кнопки подтверждения области нет. Основная страница читает реальные записи.
Логи `376308b` подтвердили Windows/Linux — 1170 passed, 34 skipped и настоящий индекс.
Текущее расширение добавляет компактный каталог, пакетное удаление, SHA-дедупликацию,
ручные версии по разделам нормативки и подготовку наборов дообучения.
Рабочий E выключен до оценки и подключения проверенных версий; обучение VLM ещё предстоит.
Контракты: [индекс](docs/experience-index.md), [версии](docs/experience-versions.md).

## 12. Подтверждение и исправление областей

```mermaid
flowchart TD
    F["VLM finding, stable finding_id"] --> S{"Server-localized region exists?"}
    S -->|нет| U["Unlocated: Review/PDF text; rejected in catalog without crop; no training"]
    S -->|да| C["Show candidate region to engineer"]
    C --> CONF{"Engineer confirms correct location?"}
    CONF -->|нет| FIX["Engineer redraws / corrects region"]
    FIX --> RECONF["Validate page, coordinates, actor, revision"]
    CONF -->|да| RECONF
    RECONF --> AREA["ConfirmedFindingArea: отдельный серверный аудит"]
    AREA --> PICK["SelectExperience candidate"]
    G["Manual Gold"] --> M["User draws issue+callout on the same page"]
    M --> A{"Gold explicitly accepted?"}
    A -->|нет| LOG["Review audit only"]
    A -->|да| PICK
```

`status=located` от локализатора не равен решению инженера. Mapper сохраняет
серверные области в `proposed_regions`; зелёная галочка принимает текущую
сохранённую область вместе с замечанием. PostgreSQL проверяет связь подтверждения
с актуальной редакцией finding. Отдельных кнопок подтверждения и запросов причины
правки области в UI нет. Крестик новую область не подтверждает. Bad без прежней
актуальной области виден в каталоге, но исключён из обучения. Сервер не придумывает
координаты; Gold остаётся на выбранном листе.

## 13. Как будет происходить отбор, отсечение и классификация

```mermaid
flowchart TD
    START["Approved current Review revision"] --> COMPLETE{"Review complete and explicitly approved?"}
    COMPLETE -->|нет| STOP["Stop: no Experience candidates"]
    COMPLETE -->|да| SOURCE{"Source?"}
    SOURCE -->|VLM| VA{"Explicitly confirmed regions?"}
    VA -->|нет| AUDIT["Review audit; accepted text PDF; rejected catalog without crop; no training"]
    VA -->|да| DEC{"Decision and correction"}
    SOURCE -->|Manual Gold| GA{"Gold accepted with valid same-page geometry?"}
    GA -->|нет| AUDIT
    GA -->|да| GOLD["tag=gold, positive candidate"]
    DEC -->|Original accepted| WISE["tag=wise, positive candidate"]
    DEC -->|Original rejected| BAD["tag=bad, negative candidate"]
    DEC -->|Edited accepted| EDIT["tag=edited, positive candidate"]
    DEC -->|Edited rejected| ADJ["tag=edited, decision=rejected: needs_adjudication"]
    WISE --> ASSET["Versioned crop + provenance + normative link"]
    BAD --> ASSET
    EDIT --> ASSET
    GOLD --> ASSET
    ADJ --> HOLD["Store for manual adjudication; never auto-train"]
    HOLD --> ASSET
    ASSET --> DEDUP["Idempotent example_key and source SHA256"]
    DEDUP --> STORE["Experience catalog: immutable source + audited curation"]
    STORE --> INDEX["Инженер выбирает раздел и состав; Knowledge строит ручную версию"]
```

Замечание без подтверждённой области **может присутствовать в операционном Review и утверждённом текстовом PDF**, но **не** попадает в обучающую Experience DB независимо от принятия. Отредактированный VLM имеет `tag=edited`, даже при `decision=rejected`; отдельный `edited-bad` как значение поля не требуется.

В каталоге `edited + rejected` остаётся `needs_adjudication`, пока инженер
не задаст причину и `negative_target=original|revised|both`. Уточнение сохраняется
в аудите; редактирование выбранной исправленной формулировки требует нового уточнения.

## 14. Экспорт Reviewed PDF и обучение

```mermaid
flowchart TD
    REV["Approved revision"] --> LOCK{"Any pending findings or stale approval?"}
    LOCK -->|да| DENY["Block reviewed PDF at API and UI"]
    LOCK -->|нет| FILTER["Include accepted only"]
    FILTER --> PAGE{"Current confirmed VLM region or accepted Gold geometry?"}
    PAGE -->|да| DRAW["Annotation on original PDF page"]
    PAGE -->|нет| NO_BOX["No artificial annotation"]
    DRAW --> REPORT["Text report for every accepted finding"]
    NO_BOX --> REPORT
    REPORT --> CACHE["Separate cache: job + approved_revision + source SHA256 + confirmation digest + renderer version"]
    CACHE --> CHECK["Recheck manifest, access and original PDF before response"]
    CHECK --> CHANGED{"Changed during export?"}
    CHANGED -->|да| CONFLICT["409: reload current Review"]
    CHANGED -->|нет| DOWNLOAD["Download current Reviewed PDF"]
    REV --> SELECT["Verified ExperienceCandidate selection"]
    SELECT --> CROPS["Source PDF crop and full versioned metadata: Experience catalog"]
    CROPS --> INDEX["Knowledge: текст + crop через shared-embedding"]
    INDEX --> EVAL["Blind retrieval evaluation and human checks"]
    EVAL --> FLAG{"Enable E feature flag only after validation"}
    FLAG --> TRAIN["Separate VLM fine-tuning research — not live"]
```

Кнопка «Утвердить и скачать итоговый PDF после Human Review» блокируется при
нерассмотренных замечаниях и незавершённом сохранении. Принятые VLM без
подтверждённой области включаются только в полный текстовый список; Gold —
на лист и в список с одинаковым номером. Отклонённые замечания сохраняются
в операционном аудите, но исключаются из итогового PDF. Подтверждение/отзыв
области меняет ключ кеша независимо от ревизии Review.

# Как работает retrieval

## N — normative retrieval

Snapshot содержит `document_ids`.

Knowledge Service допускает только документы:

- существующие;
- из выбранного section;
- `catalog_area=normative`;
- `index_status=ready`.

После этого Qdrant ищет только по exact IDs.

Найденные источники получают локальные IDs:

```text
N1, N2, N3, ...
```

## T — Technical Assignment retrieval

Если snapshot содержит `technical_assignment_id`, workflow вызывает:

```text
/internal/v1/search/technical-assignment-guided
```

Guided retrieval выполняет:

```text
query
  -> T multimodal search
  -> extract/use normative_refs and T context
  -> targeted N retrieval
  -> general N retrieval
  -> merge without changing source roles
```

Возвращаются отдельно:

```text
technical_assignment_sources
normative_sources
targeted_normative_sources
general_normative_sources
reference_resolutions
conflict_candidates
diagnostics
```

`conflict_candidates` — это T/N пары, которые Analysis Service должен оценить семантически, а не считать конфликтом только по строковому совпадению.

## U — User Package retrieval

Snapshot отдельно содержит `user_package_document_ids`.

Допускаются только документы:

- существующие;
- из того же section;
- `catalog_area=user_package`;
- `index_status=ready`.

Найденные источники получают IDs:

```text
U1, U2, U3, ...
```

## E — Experience retrieval

Контракт Experience search вызывается после requirement check по `experience_query`, но **в текущей конфигурации фактический поиск выключен** (`KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=false`). Включение только после сохранения подтверждённых crop/metadata, trusted E indexing и проверки качества. E никогда не становится нормативным basis.

Ручные версии индекса Human Review строятся отдельным процессом Knowledge.
Он получает актуальные примеры через закрытый HTTP Experience, векторизует текст
с PNG области и повторно проверяет редакцию перед записью/выдачей. Операторский
preview не включает E в обычный анализ. Отложенного размеченного набора пока нет;
полная приёмка требует парного эксперимента и совместимого отчёта качества.
Схема, критерии и развёртывание: [docs/experience-index.md](docs/experience-index.md).
Выбор состава, нормативных разделов и наборов дообучения:
[docs/experience-versions.md](docs/experience-versions.md).

# Формирование N/T/U JSON

## Retrieval JSON

Типы источников передаются независимо:

```json
{
  "normative_sources": [
    {
      "source_id": "N1",
      "point_id": "<qdrant-point>",
      "score": 0.86,
      "document_id": "<uuid>",
      "section_id": "<uuid>",
      "category_id": "<uuid-or-null>",
      "source_sha256": "<sha256>",
      "source_file": "СП 256.1325800.2016.pdf",
      "page": 17,
      "chunk_index": 2,
      "text": "<normative fragment>"
    }
  ],
  "technical_assignment_sources": [
    {
      "source_id": "T1",
      "point_id": "<qdrant-point>",
      "score": 0.83,
      "technical_assignment_id": "<uuid>",
      "analysis_document_id": "<uuid>",
      "section_id": "<uuid>",
      "source_sha256": "<sha256>",
      "source_file": "Техническое задание.pdf",
      "page": 4,
      "text": "<T requirement>",
      "normative_refs": [
        "СП 256.1325800.2016"
      ]
    }
  ],
  "user_package_sources": [
    {
      "source_id": "U1",
      "point_id": "<qdrant-point>",
      "score": 0.79,
      "document_id": "<uuid>",
      "section_id": "<uuid>",
      "category_id": "<uuid-or-null>",
      "source_sha256": "<sha256>",
      "source_file": "Требования заказчика.pdf",
      "page": 2,
      "chunk_index": 0,
      "text": "<user requirement>"
    }
  ]
}
```

`source_id` является typed local evidence ID текущего analysis context.

## FindingDraft

VLM получает отдельные N/T/U blocks и возвращает ссылки только на IDs из текущего retrieval context.

Пример T-backed finding:

```json
{
  "finding_id": "F1",
  "page": 3,
  "page_type": "scheme",
  "category": "customer_requirements",
  "severity": "warning",
  "status": "confirmed",
  "comment": "На листе отсутствует обозначение, требуемое техническим заданием.",
  "evidence": "На анализируемом листе обозначение не обнаружено.",
  "recommendation_draft": "Добавить обозначение в соответствии с ТЗ.",
  "confidence": 0.91,

  "normative_source_ids": [],
  "technical_assignment_source_ids": [
    "T1"
  ],
  "user_package_source_ids": [],

  "basis": "Требование технического задания.",
  "experience_query": "Проверка оформления обозначений"
}
```

Backend материализует IDs только через текущие retrieval candidates. Произвольный `N99/T99/U99`, которого не было в retrieval context, не должен превращаться в источник.

## Typed evidence materialization

```json
{
  "finding_id": "F1",
  "category": "customer_requirements",
  "basis_sources": [],
  "technical_assignment_basis_sources": [
    {
      "source_id": "T1",
      "technical_assignment_id": "<uuid>",
      "source_file": "Техническое задание.pdf",
      "page": 4,
      "text": "<T requirement>"
    }
  ],
  "user_package_basis_sources": []
}
```

## Finding-local normative enrichment

После первичного requirement check инженерное замечание не удаляется из-за отсутствия N.

Для каждого finding может выполняться отдельный targeted N retrieval:

```text
finding
  -> finding-local normative query
  -> N candidates
  -> semantic validation/finalization
```

Пример:

```text
T1
  -> упоминание/смысл СП 256
  -> targeted normative retrieval
  -> N4
```

После подтверждения:

```json
{
  "finding_id": "F1",
  "category": "normative_control",
  "basis_sources": [
    {
      "source_id": "N4",
      "source_file": "СП 256.1325800.2016.pdf",
      "page": 17,
      "text": "<validated normative fragment>"
    }
  ],
  "technical_assignment_basis_sources": [
    {
      "source_id": "T1",
      "source_file": "Техническое задание.pdf",
      "page": 4,
      "text": "<project requirement>"
    }
  ]
}
```

Здесь `T1` остаётся T-source, а нормативное утверждение подтверждает `N4`.

## FinalFinding

Финальный finding хранит источники раздельно:

```json
{
  "finding_id": "F1",
  "page": 3,
  "page_type": "scheme",
  "category": "normative_control",
  "severity": "warning",
  "status": "confirmed",
  "comment": "<finding>",
  "evidence": "<drawing evidence>",
  "recommendation": "<recommendation>",
  "confidence": 0.93,
  "basis": "<human-readable basis>",

  "basis_sources": [
    {
      "source_id": "N1"
    }
  ],
  "technical_assignment_basis_sources": [
    {
      "source_id": "T1"
    }
  ],
  "user_package_basis_sources": [
    {
      "source_id": "U1"
    }
  ],
  "experience_sources": [
    {
      "source_id": "E1"
    }
  ]
}
```

# Пояснительная записка

```mermaid
flowchart TD
    RANGE["Диапазон страниц ПЗ"] --> TEXT["Text extraction"]
    TEXT --> VALID["Classification"]
    VALID -->|не ПЗ| REJECT["Reject"]
    VALID -->|ПЗ| CHUNK["Chunking"]
    CHUNK --> EMB["Unified Embeddings"]
    EMB --> TEMP[("Temporary Project Context")]
    PAGE["Анализируемый лист"] --> QUERY["Context query"]
    QUERY --> TEMP
    TEMP --> SOURCES["Relevant fragments"]
    SOURCES --> CHECK["N/T/U check"]
    CHECK --> CLEAN["Cleanup"]
    CLEAN --> DELETE["Delete temporary collection"]
```

Project Context помогает понять проект, но не становится N/T/U evidence.

# Управляемый каталог

## Lifecycle N/U документа

```text
upload
  ↓
uploaded
  ↓
queued
  ↓
indexing
  ├──→ ready
  └──→ failed
```

Из `failed` документ можно повторно поставить в queue.

Удаление переводит document в `deleting`, затем удаляет:

1. Qdrant points по `document_id`;
2. original file;
3. Word PDF-preview;
4. SQL metadata.

## System prompt

Используются три уровня:

```text
NORMATIVE_SUPER_SYSTEM_PROMPT
        +
section.system_prompt
        +
transient working override / dynamic context
```

`NORMATIVE_SUPER_SYSTEM_PROMPT` хранится в коде.

`section.system_prompt` хранится в PostgreSQL.

Transient working prompt применяется к конкретному анализу без сохранения как новый system prompt.

# Пользовательские пакеты документов

Пакеты относятся к нормативному section, но имеют:

```text
catalog_area=user_package
```

Frontend позволяет:

- создать пакет/папку;
- создать вложенную папку;
- загрузить PDF/DOC/DOCX;
- автоматически поставить документ в indexing queue;
- открыть PDF или Word PDF-preview;
- перемещать документы;
- удалять документы/папки;
- выбирать отдельные READY документы;
- выбирать все READY package docs;
- очищать selection.

# Публичный API

## Analysis

```text
POST /api/v1/analyses
GET  /api/v1/analyses/{job_id}
GET  /api/v1/analyses/{job_id}/result
GET  /api/v1/analyses/{job_id}/progress
POST /api/v1/analyses/{job_id}/cancel
GET  /api/v1/analyses/{job_id}/visualization
GET  /api/v1/analyses/{job_id}/annotated-pdf  # automatic, not reviewed
POST /api/v1/analyses/{job_id}/reviewed-pdf  # закрытый Review frontend, JSON: expected_revision
```

Multipart analysis fields включают:

```text
pdf
cad
pages
use_explanatory_note
note_start_page
note_end_page
normative_section_id
normative_document_ids
user_package_document_ids
normative_prompt_override_enabled
normative_prompt_override
technical_assignment
```

Точная форма T upload определяется public analysis schema/frontend contract; внутри immutable snapshot сохраняется `technical_assignment_id`.

## Managed normative catalog

```text
GET    /api/v1/normative/sections
POST   /api/v1/normative/sections
GET    /api/v1/normative/sections/{section_id}
PATCH  /api/v1/normative/sections/{section_id}
DELETE /api/v1/normative/sections/{section_id}

GET    /api/v1/normative/sections/{section_id}/categories
POST   /api/v1/normative/sections/{section_id}/categories
GET    /api/v1/normative/categories/{category_id}
PATCH  /api/v1/normative/categories/{category_id}
DELETE /api/v1/normative/categories/{category_id}

GET    /api/v1/normative/sections/{section_id}/documents
POST   /api/v1/normative/sections/{section_id}/documents
GET    /api/v1/normative/documents/{document_id}
PATCH  /api/v1/normative/documents/{document_id}
DELETE /api/v1/normative/documents/{document_id}

POST   /api/v1/normative/documents/{document_id}/index
GET    /api/v1/normative/documents/{document_id}/content
```

## User packages

```text
GET    /api/v1/normative/sections/{section_id}/user-packages/categories
POST   /api/v1/normative/sections/{section_id}/user-packages/categories

GET    /api/v1/normative/user-packages/categories/{category_id}
PATCH  /api/v1/normative/user-packages/categories/{category_id}
DELETE /api/v1/normative/user-packages/categories/{category_id}

GET    /api/v1/normative/sections/{section_id}/user-packages/documents
POST   /api/v1/normative/sections/{section_id}/user-packages/documents

GET    /api/v1/normative/user-packages/documents/{document_id}
PATCH  /api/v1/normative/user-packages/documents/{document_id}
DELETE /api/v1/normative/user-packages/documents/{document_id}

POST   /api/v1/normative/user-packages/documents/{document_id}/index
GET    /api/v1/normative/user-packages/documents/{document_id}/content
```

## Technical Assignment content

Кликабельный T-source открывается через Gateway:

```text
GET /api/v1/normative/technical-assignments/{technical_assignment_id}/content
```

Browser не обращается к internal Knowledge/Experience API напрямую. Закрытый Review API доступен через Gateway на приватном frontend; Reviewed PDF доступен только для текущей утверждённой редакции. Постоянный каталог Experience, crop, CRUD и экспорт примеров реализованы через закрытый Gateway API. Примеры с отозванным подтверждением или изменённым Review сохраняются для аудита, но становятся неактуальными и исключаются из пригодного для обучения набора.

# Рабочие процессы n8n

Repository:

```text
n8n/workflows/
├── analysis-v2-pdf.json
├── analysis-v2-cad.json
└── analysis-v2-pdf-cad.json
```

В V2 PDF выполняются stage-scoped операции с последовательной обработкой страниц тяжёлых этапов; CAD/PDF+CAD сохраняют свои source-mode правила. Общий requirement path:

```text
Page understanding
  -> Build Normative Queries
  -> Search Requirements
       ├─ no T -> normative search
       └─ T    -> technical-assignment-guided search
  -> Normalize Requirement Search
  -> Search User Packages
  -> Check Norms
  -> Prepare Finding Normative Queries
  -> Search Finding Norms
  -> Search Experience (выключен до оценки на отложенных документах)
  -> Group/Finalize Findings
```

`Check Norms` получает typed sources отдельно:

```json
{
  "normative_sources": [],
  "technical_assignment_sources": [],
  "conflict_candidates": [],
  "user_package_sources": []
}
```

n8n не присваивает T/U нормативную роль. Он только переносит typed data между сервисами.

Transient HTTP errors на GPU-dependent retrieval nodes должны иметь bounded retry policy; основная resource-serialization логика остаётся в backend GPU coordinator, а не в workflow.

Workflow обновляются и публикуются вручную через n8n UI.

# Структура проекта

```text
PDRD-validation/
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── src/
│       ├── index.html
│       ├── css/
│       └── js/
│           ├── app.js
│           ├── config.js
│           ├── components/
│           └── features/
│               ├── analysis/
│               ├── normative/
│               └── review/     # Wise/Bad/Edited/Gold + manual geometry/text list
│
├── services/
│   ├── api-gateway/
│   │   ├── alembic/
│   │   ├── src/pdrd_api_gateway/
│   │   └── tests/
│   ├── document-service/
│   ├── knowledge-service/
│   │   ├── alembic/
│   │   ├── src/pdrd_knowledge_service/
│   │   │   ├── application/
│   │   │   ├── domain/
│   │   │   └── infrastructure/
│   │   │       ├── database/
│   │   │       ├── embedding/
│   │   │       ├── messaging/
│   │   │       ├── migration/
│   │   │       └── vector_store/
│   │   └── tests/
│   ├── analysis-service/
│   │   ├── src/pdrd_analysis_service/
│   │   │   ├── application/ports/gpu.py
│   │   │   └── infrastructure/
│   │   │       ├── gpu_coordination.py
│   │   │       └── ollama.py
│   │   └── tests/
│   ├── multimodal-embedding-service/  # legacy/test code; not a Compose runtime
│   └── experience-service/
│       ├── alembic/             # Experience-only PostgreSQL revisions
│       ├── src/pdrd_experience_service/
│       │   ├── domain/          # ReviewSession, ExperienceCandidate
│       │   ├── application/     # open/change/select ports and use cases
│       │   └── infrastructure/  # PostgreSQL persistence (current stage)
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
│       └── experience/
│           └── cases/
│
├── ops/
├── scripts/
│   ├── build_experience_cases.py
│   ├── check-stack.sh
│   ├── kb_common.py
│   ├── kb_search.py
│   └── kb_sync.py
├── tests/
│   ├── architecture/
│   └── runtime/
│       └── test_gpu_coordination_runtime.py
├── .env.example
├── compose.yaml
├── pyproject.toml
├── requirements-dev.txt
└── README.md
```

# Конфигурация

`.env.example` — committed baseline и полный каталог ordinary runtime settings.

`.env` — sparse private override. В обычном deployment в нём достаточно секретов:

```dotenv
PDRD_POSTGRES_PASSWORD=replace-me
PDRD_RABBITMQ_PASSWORD=replace-me
```

`.env` не должен быть копией `.env.example`.

Единая embedding identity:

```dotenv
PDRD_EMBEDDING_MODEL=shared-embedding
PDRD_EMBEDDING_DIMENSION=4096
PDRD_EMBEDDING_SCHEMA_VERSION=2
```

Физическая модель и GPU topology управляются shared infrastructure; смена логической model identity, размерности или версии схемы требует контролируемого fingerprint cutover. `.env.example` содержит baseline, `.env` — sparse private overrides, и process/Compose env учитываются по согласованному приоритету.

Ключевые Knowledge defaults:

```text
storage.root_path = /data/normative
qdrant.normative_collection = dva_catalog_active
qdrant.multimodal_collection = dva_technical_assignment_active
qdrant.experience_collection = dva_experience_active
project_context.collection_prefix = pdrd_project_context
embedding.model = PDRD_EMBEDDING_MODEL
broker.queue_name = pdrd.knowledge.indexing
```

Ключевые runtime defaults:

```text
analysis VLM URL       = http://shared-vlm:8000/v1
analysis VLM alias     = shared-vlm
embedding URL          = http://shared-embedding:8000/v1
embedding alias        = shared-embedding
project GPU lease path = /var/lock/pdrd-gpu/gpu.lock (where applicable)
Experience E search    = выключен до отложенной оценки качества
```

# Запуск

Требуются Docker Engine и Docker Compose plugin.

Shared network `ai-shared` должна предоставлять RabbitMQ, n8n, `shared-vlm` и `shared-embedding`; их lifecycle независим от проекта. Физические checkpoint/model IDs и GPU layout настраиваются в `shared-infrastructure`, а проект использует logical aliases.

Безопасный штатный deploy следует выполнять штатным проектным скриптом после проверки текущей ветки/контейнеров и Compose: `bash scripts/up.sh`. Не перезапускайте shared stack и не удаляйте volume ради разработки Experience.

Experience подключён к Compose только профилем `review`. Сначала общие и изолированные SQL-тесты, затем настройка серверного канала и явная миграция по [docs/review-api.md](docs/review-api.md). Основной frontend 8080 использует серверный Review и каталог Experience. Рабочий адрес — `http://192.168.55.3:8080/`; SSH-туннель не требуется. Ключи остаются между контейнерами. Пользовательская авторизация, AD и локальный суперпользователь включаются профилями `identity,auth`: см. [docs/identity-auth-service.md](docs/identity-auth-service.md).

Для подключения ручной индексации после синхронизации ветки:
`bash ops/deploy-experience-index.sh </dev/null`. Скрипт повторяет общий и SQL gate,
создаёт отдельный индексный ключ, применяет миграцию `20260929_0004` и запускает
worker ручной очереди профилем `experience-index`, проверяет выключенный рабочий E.
Прежний автоматический worker останавливается до обновления. Подробности —
[docs/experience-index.md](docs/experience-index.md).

Обновление этапа 6 в уже настроенном закрытом окружении после синхронизации
ветки выполняется одной командой `bash ops/deploy-reviewed-pdf.sh </dev/null`.
Скрипт проверяет общий набор и изолированный PostgreSQL перед пересозданием
Gateway, Experience, Document Service и обоих фронтов. Fetch/merge выполняется
отдельно; shared-сервисы и рабочие volumes не меняются.

При startup `knowledge-embedding-migrator` проверяет embedding fingerprint до старта Knowledge runtime.

Проверка:

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

Knowledge Service Swagger:

```text
http://127.0.0.1:8401/docs
```

Обычная остановка:

```bash
docker compose down
```

Не использовать для обычного deploy:

```bash
docker compose down -v
```

# Тестирование

Windows quality gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\check-quality.ps1
```

Скрипт проверяет Python и Node, `pip check`, Ruff check/format,
общий pytest с новым `--basetemp`, все `frontend/tests/*.test.js` через Node
и `git diff --check`.
Параметр `-Fix` применяет исправления Ruff перед проверками.
Для коммита и push после успешной проверки на `feature/experience-base`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\check-quality.ps1 -CommitMessage "feat: export approved human review PDF" -Push
```

`-CommitMessage` включает все неигнорируемые изменения рабочей копии;
`-Push` использует системный OpenSSH и push-адреса `origin`.
При отдельном remote `neoterm` приватную ветку отправляют командой
`git push neoterm feature/experience-base` после успешного quality gate.
Ненулевой код любого шага останавливает скрипт.
`ExecutionPolicy Bypass` действует только для этого запуска PowerShell.

Docker quality:

```bash
docker compose --profile test build quality-tests
docker compose --profile test run --rm --no-deps quality-tests
```

Review domain/Experience selection и инфраструктурные тесты:

```powershell
.\.venv\Scripts\python.exe -m pytest services/experience-service/tests -q
cd frontend; node --test tests/*.test.js; cd ..
```

PostgreSQL integration Experience выполняется только на отдельной тестовой БД и только после Alembic upgrade с явными `PDRD_RUN_DATABASE_TESTS=1` и `EXPERIENCE_SERVICE_TEST_DATABASE_URL` (не на production DB).

Service integration:

```bash
docker compose --profile test build api-gateway-tests knowledge-service-tests
docker compose --profile test run --rm api-gateway-tests
docker compose --profile test run --rm knowledge-service-tests
```

GPU runtime coordination:

```bash
docker compose --profile gpu-runtime-test run --rm gpu-runtime-tests
```

Проверка stack:

```bash
bash scripts/check-stack.sh
```

# Backup и диагностика

Посмотреть volumes:

```bash
docker volume ls | grep -E 'postgres|qdrant|analysis|normative|technical|gpu'
```

Managed SQL documents:

```bash
docker compose exec -T postgres sh -lc '
psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -P pager=off \
  -c "
SELECT catalog_area, index_status, count(*)
FROM knowledge.normative_documents
GROUP BY catalog_area, index_status
ORDER BY catalog_area, index_status;
"
'
```

Technical Assignments:

```bash
docker compose exec -T postgres sh -lc '
psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -P pager=off \
  -c "
SELECT index_status, count(*)
FROM knowledge.technical_assignments
GROUP BY index_status
ORDER BY index_status;
"
'
```

Qdrant aliases:

```bash
curl -fsS http://127.0.0.1:6333/aliases | python3 -m json.tool
```

Embedding runtime:

```bash
curl -fsS http://127.0.0.1:8601/internal/v1/status | python3 -m json.tool
```

Shared runtime checks осуществляются через опубликованные health endpoints `shared-vlm` и `shared-embedding` в доверенной LAN/VPN. Их адреса, модельный alias и topology следует проверять по текущему `shared-infrastructure/docs/services.yaml`, а не по локальному Ollama `api/ps`.

# Текущий функциональный контур

В проекте реализованы:

- V2 PDF/CAD/PDF+CAD runtime;
- PDF + ПЗ и PDF+CAD + ПЗ;
- Gateway -> Outbox -> RabbitMQ -> Celery -> n8n;
- managed N/U catalog;
- Technical Assignment lifecycle и отдельный T-index;
- immutable T snapshot и READY barrier перед analysis;
- multimodal T retrieval;
- T-guided normative retrieval;
- N/T/U typed evidence;
- finding-local normative enrichment;
- Experience search contract присутствует в finalization; реально отключён флагом до проверенной записи Experience;
- shared vLLM vision и embedding logical endpoints, 4096-dimension vector contract;
- stable Qdrant aliases и model fingerprint;
- blue/green automatic reindex из durable sources;
- project-side GPU lease/admission where used; shared model residency управляется отдельным shared-runtime lifecycle;
- temporary Project Context cleanup;
- кликабельные N/T sources через API Gateway;
- frontend нормативного каталога, пользовательских пакетов и ТЗ;
- Human Review frontend: Wise/Bad/Edited/Gold, независимые решения сгруппированных findings, создание Gold с двумя областями и общим текстовым списком;
- доменная модель `ReviewSession`, журнал редакций, optimistic revisions, подтверждаемая геометрия и `SelectExperience` (пока только подготовка кандидатов);
- отдельная SQLAlchemy PostgreSQL persistence и Experience Alembic migration, закрытый Review API, серверный источник анализа и mapper предложенных VLM-областей;
- автоматический PDF и отдельный Reviewed PDF после Human Review; итоговый экспорт требует актуального утверждения и включает только accepted;
- unit/integration/architecture/runtime test layers.

**Текущая приёмка:** этапы 5–7 и первый индекс этапа 8 подтверждены логами пользователя.
Текущее расширение этапа 8 заменяет автоматический обход ручными версиями,
добавляет нормативные разделы, выбор/удаление и дедупликацию повторных прогонов.
Сохранение каталога выполняется при утверждении PDF; отдельной кнопки нет.
Рабочий E выключен до парной оценки на отложенном наборе и подключения рабочих версий.
В этапе 9 сейчас реализован реестр/подготовка; фактическое обучение и смена весов впереди.

Принятие замечания одной зелёной галочкой принимает его текущую сохранённую область:
Review и аудит координат фиксируются атомарно. Отдельной кнопки проверки области
и запроса причины изменения рамки нет. Изменение только геометрии сохраняет Wise,
правка текста или основания даёт Edited, ручное замечание остаётся Gold.
Крестик не подтверждает новую область: Bad сохраняется в каталоге и без области,
но в обучающий набор попадает только с ранее принятой и актуальной областью.
Замечания без области сохраняются в Review;
принятые присутствуют в текстовой части итогового PDF, без придуманной рамки.
