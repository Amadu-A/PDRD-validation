# PDRD Validation — Drawing Validation AI

MVP-сервис проверки проектной и рабочей документации по нормативной базе с локальными VLM/embeddings, RAG и Базой Опыта.

## Архитектура

```text
Browser
  -> Frontend nginx
  -> API Gateway
      -> PostgreSQL + transactional outbox
      -> RabbitMQ (только job_id)
          -> Celery worker
              -> n8n
                  -> Document Service
                  -> Knowledge Service -> Qdrant
                  -> Analysis Service -> Ollama
```

Браузер не обращается напрямую к n8n или внутренним микросервисам.

## Поддерживаемые режимы

- PDF-only;
- CAD-only: DXF и DWG с нормализацией DWG -> DXF;
- PDF + CAD как два представления одного листа;
- PDF + контекст Пояснительной записки;
- PDF + CAD + контекст Пояснительной записки.

Для PDF + CAD необходимо выбрать ровно одну PDF-страницу, соответствующую CAD-файлу. Контекст ПЗ доступен только при наличии PDF.

## Контекст Пояснительной записки

Pipeline ПЗ:

1. Document Service валидирует физический диапазон PDF-страниц и извлекает текст без рендера.
2. Analysis Service классифицирует выбранные страницы. Страницы, уверенно определённые как чертёж, спецификация или другой материал вместо ПЗ, отклоняют диапазон.
3. Knowledge Service нормализует текст, разбивает его на перекрывающиеся chunks и строит document embeddings.
4. Chunks помещаются во временную Qdrant collection, детерминированную по `document_id`.
5. Для каждого анализируемого листа строится отдельный Project Context query по фактам листа.
6. Semantic search возвращает наиболее релевантные PZ-фрагменты.
7. ПЗ добавляется как контекст проекта, но не считается нормативным доказательством.
8. Нормативное нарушение должно подтверждаться реальным нормативным `N-id`.
9. Временная collection удаляется после анализа. n8n выполняет штатный cleanup, Gateway worker — идемпотентный страховочный cleanup.

## API Gateway

Ответственность:

- публичный multipart API;
- валидация заявки;
- временное хранение документов через application port;
- PostgreSQL job state;
- transactional outbox;
- RabbitMQ/Celery;
- запуск опубликованного n8n V2 workflow;
- status/result API;
- страховочный cleanup Project Context.

Публичные endpoints:

```text
POST /api/v1/analyses
GET  /api/v1/analyses/{job_id}
GET  /api/v1/analyses/{job_id}/result
GET  /health/live
GET  /health/ready
```

RabbitMQ получает только `job_id`. PDF/DWG/DXF bytes через очередь не передаются.

## Document Service

Ответственность:

- PDF page selection;
- PDF text extraction и PNG render;
- text-only extraction диапазона ПЗ;
- DXF parsing;
- DWG -> DXF normalization;
- CAD machine context и render;
- combined PDF + CAD render.

Internal endpoints:

```text
POST /internal/v1/pdf/extract
POST /internal/v1/cad/extract
POST /internal/v1/combined/extract
GET  /health/live
GET  /health/ready
```

## Knowledge Service

Ответственность:

- embeddings через shared Ollama;
- нормативный semantic search;
- Experience semantic search;
- временный Project Context create/search/delete;
- Qdrant только через infrastructure adapter.

Internal endpoints:

```text
POST   /internal/v1/search/normative
POST   /internal/v1/search/experience
POST   /internal/v1/project-contexts
POST   /internal/v1/project-contexts/search
DELETE /internal/v1/project-contexts/{context_id}
GET    /health/live
GET    /health/ready
```

## Analysis Service

Ответственность:

- structured understanding листа;
- классификация диапазона ПЗ;
- построение Project Context query;
- безопасное добавление ПЗ к analysis text;
- построение normative queries;
- нормативная VLM-проверка;
- финализация findings с Базой Опыта.

Analysis Service не обращается напрямую к Qdrant или Knowledge Service. Оркестрацию выполняет n8n.

## n8n

n8n является shared infrastructure.

Рабочие V2 workflow:

```text
n8n/workflows/analysis-v2-pdf.json
n8n/workflows/analysis-v2-cad.json
n8n/workflows/analysis-v2-pdf-cad.json
```

Публичный клиент их не вызывает: n8n вызывается Gateway worker.

## Frontend

```text
frontend/
├── Dockerfile
├── nginx.conf
└── src/
    ├── index.html
    ├── css/
    │   ├── variables.css
    │   ├── global.css
    │   ├── main.css
    │   └── blocks/
    └── js/
        ├── app.js
        ├── config.js
        ├── components/
        └── features/
            └── analysis/
```

`app.js` — composition root. API, polling, form state, rendering и UI-components разделены по ответственности.

## Инфраструктура

Shared:

- Ollama;
- RabbitMQ;
- n8n;
- PostgreSQL n8n.

Project-specific:

- PostgreSQL PDRD;
- Qdrant;
- API Gateway;
- Celery worker;
- outbox dispatcher;
- Document Service;
- Knowledge Service;
- Analysis Service;
- Frontend.

Сети:

```text
app-net
ai-shared
```

Frontend подключается только к `app-net`. Shared network получают только сервисы, которым действительно нужны shared dependencies.

## Модели

Server defaults:

```text
qwen3-vl:8b-instruct
qwen3-embedding:4b
```

## Qdrant

Постоянные collections:

```text
dva_normative_v2
dva_experience_v2
```

Временный контекст ПЗ:

```text
pdrd_project_context_<document_uuid_without_hyphens>
```

После завершения или ошибки job временная collection должна отсутствовать.

## Порты

```text
Frontend          :8080
API Gateway       127.0.0.1:8200
Document Service  127.0.0.1:8301
Knowledge Service 127.0.0.1:8401
Analysis Service  127.0.0.1:8501
PostgreSQL        0.0.0.0:5432
Qdrant            :6333 / :6334
n8n shared        :5678
Ollama shared     :11434
```

## Конфигурация

```bash
test -f .env || cp .env.example .env
```

Реальные пароли не коммитить.

Основные Pydantic Settings prefixes:

```text
API_GATEWAY_*
DOCUMENT_SERVICE_*
KNOWLEDGE_SERVICE_*
ANALYSIS_SERVICE_*
```

Nested settings используют `__`, например:

```text
DOCUMENT_SERVICE_PDF__MAX_CONTEXT_PAGES
KNOWLEDGE_SERVICE_PROJECT_CONTEXT__TOP_K
ANALYSIS_SERVICE_PROJECT_CONTEXT__CLASSIFY_BATCH_SIZE
```

## Запуск на сервере

```bash
cd ~/projects/PDRD-validation
git pull --ff-only
docker compose config --quiet
docker compose build   api-gateway   api-gateway-worker   api-gateway-outbox   document-service   knowledge-service   analysis-service   frontend

docker compose up -d   --force-recreate   api-gateway   api-gateway-worker   api-gateway-outbox   document-service   knowledge-service   analysis-service   frontend
```

Проверка:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8200/health/ready
curl -fsS http://127.0.0.1:8301/health/ready
curl -fsS http://127.0.0.1:8401/health/ready
curl -fsS http://127.0.0.1:8501/health/ready
```

## Quality

Windows:

```powershell
.\ops\check-quality.ps1 -Fix
```

Server:

```bash
docker compose --profile test build --no-cache quality-tests
docker compose --profile test run --rm quality-tests
```

Architecture tests контролируют:

- направление backend dependencies;
- relative-path headers Python source-файлов;
- frontend `src` structure;
- отсутствие browser -> n8n/legacy references;
- CSS layering.

## Финальная Stage 1 parity matrix

Перед удалением legacy необходимо подтвердить:

```text
PDF
multi-page PDF
CAD
PDF + CAD
PDF + ПЗ
PDF + CAD + ПЗ
invalid PZ range
CAD + ПЗ -> 422
browser E2E
temporary Qdrant context cleanup
```

## Legacy transition

На переходном коммите `services/pdf-service` и `n8n/workflows/analysis-main.json` могут физически оставаться только как rollback/parity reference. Публичный Browser -> Gateway -> V2 runtime их не использует.

После успешной parity matrix Stage 1 завершается отдельным cleanup commit:

- удалить `services/pdf-service`;
- удалить `analysis-main.json`;
- удалить legacy compose/env settings;
- удалить временный Ruff exclude;
- поднять stack без legacy;
- повторить smoke tests.

После этого Stage 1 считается завершённым. Следующий этап — viewer/render/location.
