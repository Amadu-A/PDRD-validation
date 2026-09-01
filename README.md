# PDRD Validation — Drawing Validation AI

MVP-сервис проверки проектной и рабочей документации по нормативной базе с локальными VLM/embeddings, RAG, Базой Опыта и контекстом Пояснительной записки.

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

1. Document Service валидирует физический диапазон PDF-страниц и извлекает text-only содержимое.
2. Analysis Service классифицирует выбранные страницы. Страницы, уверенно определённые как чертёж, спецификация или другой материал вместо ПЗ, отклоняют диапазон.
3. Knowledge Service нормализует текст, разбивает его на перекрывающиеся chunks и строит embeddings.
4. Chunks помещаются во временную Qdrant collection, детерминированную по `document_id`.
5. Для каждого анализируемого листа строится отдельный semantic query.
6. Semantic search возвращает наиболее релевантные фрагменты ПЗ.
7. ПЗ добавляется как контекст проекта, но не считается нормативным доказательством.
8. Нормативное нарушение должно подтверждаться реальным нормативным `N-id`.
9. Временная collection удаляется после анализа. n8n выполняет штатный cleanup, Gateway worker — идемпотентный страховочный cleanup.

## API Gateway

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

Internal endpoints:

```text
POST /internal/v1/pdf/extract
POST /internal/v1/cad/extract
POST /internal/v1/combined/extract
GET  /health/live
GET  /health/ready
```

## Knowledge Service

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

Analysis Service отвечает за structured understanding листа, классификацию ПЗ, Project Context query, normative queries, нормативную VLM-проверку и финализацию findings. Он не обращается напрямую к Qdrant или Knowledge Service: orchestration выполняет n8n.

## n8n

Рабочие V2 workflow:

```text
n8n/workflows/analysis-v2-pdf.json
n8n/workflows/analysis-v2-cad.json
n8n/workflows/analysis-v2-pdf-cad.json
```

Публичный клиент их не вызывает: n8n вызывается только Gateway worker.

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

Frontend подключается только к `app-net`. Shared network получают только сервисы, которым действительно нужны shared dependencies.

## Модели

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

Nested Pydantic Settings используют `__`, например:

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
bash scripts/check-stack.sh
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

Architecture tests контролируют направление backend dependencies, relative-path headers, frontend `src` structure, CSS layering и отсутствие удалённого legacy runtime.

## Stage 1

Stage 1 подтверждает:

```text
PDF
multi-page PDF
CAD
PDF + CAD
PDF + ПЗ
PDF + CAD + ПЗ
валидацию неправильного диапазона ПЗ
CAD + ПЗ -> 422
Gateway asynchronous job lifecycle
Browser -> Gateway
temporary Qdrant Project Context cleanup
```

Legacy `pdf-service` удалён. Рабочий runtime использует только V2-архитектуру.

Следующий этап — viewer/render/location: визуализация листа, выбор finding, normalized bbox/location и overlay.
