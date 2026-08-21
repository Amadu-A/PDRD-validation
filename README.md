# PDRD-validation — Drawing Validation AI

MVP-сервис для проверки проектной и рабочей документации по нормативной базе с использованием локальной VLM, RAG и Базы Опыта.

Текущая версия ориентирована на **PDF**. Поддержка **DXF** предусмотрена архитектурой, но пока не включена в рабочий pipeline.

### Инфраструктурная схема

PDRD использует общие инфраструктурные сервисы из отдельного
`shared-infrastructure`.

```text
shared-infrastructure
├── Ollama
├── n8n
├── RabbitMQ
└── n8n PostgreSQL
        │
        └── ai-shared
              │
              ├── PDRD frontend
              └── PDRD pdf-service
                     │
                     └── project app-net
                           └── PDRD 
```

## 1. Архитектура

```text
PDF
 ↓
извлечение текста + рендер страницы
 ↓
Qwen3-VL 8B
 ↓
структурированное понимание листа
БЕЗ поиска ошибок
 ↓
нейтральные темы нормативной проверки
 ↓
Qwen3-Embedding 4B
 ↓
Qdrant: нормативная база
 ↓
релевантные нормативные фрагменты
 ↓
Qwen3-VL 8B
 ↓
проверка листа по найденным нормам
 ↓
confirmed / needs_review
 ↓
Qwen3-Embedding 4B
 ↓
Qdrant: База Опыта
 ↓
похожие экспертные замечания
 ↓
Qwen3-VL 8B
 ↓
формулировка замечания + рекомендация
 ↓
отчёт пользователю
```

База Опыта не определяет наличие ошибки. Нарушение сначала устанавливается по текущему PDF и найденным нормативным требованиям. База Опыта используется только как пример инженерной формулировки и дополнительный контекст BEFORE → AFTER.

Если `verified_fixed=false`, AFTER-лист не считается подтверждённым способом исправления.

## 2. Стек

### PDRD

- Docker / Docker Compose
- FastAPI
- nginx
- Qdrant
- PyMuPDF
- Python

### Shared infrastructure

- Ollama
- n8n
- PostgreSQL для n8n
- RabbitMQ

### AI models

- `qwen3-vl:8b-instruct`
- `qwen3-embedding:4b`

Размерность embedding для `qwen3-embedding:4b`:

```text
2560
```

Текущие Qdrant collections:

```text
dva_normative_v2
dva_experience_v2
```

## 3. Структура проекта

```text
PDRD-validation/
├── .env
├── .env.example
├── .gitignore
├── README.md
├── compose.yaml
│
├── data/
│   ├── knowledge/
│   │   ├── normative/
│   │   │   └── source/
│   │   │       └── *.pdf
│   │   └── experience/
│   │       └── cases/
│   │           └── PROJECT_ID/
│   │               ├── before/
│   │               │   └── before.pdf
│   │               ├── after/
│   │               │   └── after.pdf
│   │               ├── dxf/
│   │               │   ├── before/
│   │               │   └── after/
│   │               └── annotations/
│   │                   ├── issues.json
│   │                   └── meta.json
│   ├── results/
│   └── uploads/
│
├── frontend/
│   ├── Dockerfile
│   ├── app.js
│   ├── index.html
│   ├── nginx.conf
│   └── styles.css
│
├── n8n/
│   └── workflows/
│       └── analysis-main.json
│
├── scripts/
│   ├── __init__.py
│   ├── build_experience_cases.py
│   ├── check-stack.sh
│   ├── check-stage2.sh
│   ├── kb_common.py
│   ├── kb_search.py
│   ├── kb_sync.py
│   ├── requirements-experience.txt
│   └── requirements-kb.txt
│
└── services/
    └── pdf-service/
        ├── Dockerfile
        ├── requirements.txt
        └── app/
            ├── __init__.py
            ├── main.py
            ├── rag.py
            └── validator.py
```

## 4. Назначение компонентов

### `frontend`

Одностраничный интерфейс:

```text
выбрать PDF
→ указать страницы или оставить поле пустым
→ Анализировать
→ получить отчёт
```

DXF пока отключён.

### `n8n`

n8n является shared infrastructure service и не запускается
Compose-файлом PDRD.

Исходник workflow хранится в репозитории:

```text
n8n/workflows/analysis-main.json
POST /analysis
 ↓
shared n8n
 ↓
http://pdrd-pdf-service:8101/analyze
```

### `services/pdf-service/app/main.py`

- FastAPI endpoints;
- чтение PDF;
- выбор страниц;
- извлечение текста;
- рендер;
- orchestration pipeline.

### `services/pdf-service/app/rag.py`

- embeddings через Ollama;
- поиск нормативов;
- поиск похожего опыта;
- Qdrant API.

### `services/pdf-service/app/validator.py`

- понимание листа;
- нормативная проверка;
- structured JSON;
- финальное оформление результата.

### `scripts/kb_sync.py`

Ручная синхронизация нормативной и опытной базы с Qdrant.

Индексация специально **не запускается автоматически** при старте Docker.

## 5. Требования

Рекомендуемое серверное окружение:

- Ubuntu Linux;
- Docker Engine;
- Docker Compose;
- NVIDIA GPU;
- NVIDIA Container Toolkit;
- Git;
- Python 3;
- запущенный `shared-infrastructure`.

Проверки:

```bash
docker --version
docker compose version
python3 --version
nvidia-smi
```

Проверка GPU внутри Docker:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

## 6. Клонирование

```bash
git clone https://github.com/Amadu-A/PDRD-validation.git
cd PDRD-validation
```

Если репозиторий уже есть:

```bash
cd ~/PDRD-validation
git pull
```

## 7. `.env`

Если `.env` ещё нет:

```bash
test -f .env || cp .env.example .env
```

`cp .env.example .env`

Основные параметры:

```dotenv
COMPOSE_PROJECT_NAME=pdrd-validation-ai
SHARED_DOCKER_NETWORK=ai-shared
SHARED_N8N_URL=http://127.0.0.1:5678

OLLAMA_BASE_URL=http://ollama:11434
KB_OLLAMA_URL=http://127.0.0.1:11434

OLLAMA_VISION_MODEL=qwen3-vl:8b-instruct
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:4b

QDRANT_NORMATIVE_COLLECTION=dva_normative_v2
QDRANT_EXPERIENCE_COLLECTION=dva_experience_v2
```

`OLLAMA_VALIDATOR_MODEL` в текущей архитектуре не используется.

## 8. Python venv для KB scripts

```bash
cd ~/PDRD-validation
python3 -m venv .venv
source .venv/bin/activate
```

Если `venv` отсутствует:

```bash
sudo apt update
sudo apt install -y python3-venv
```

Установить зависимости:

```bash
python -m pip install --upgrade pip
python -m pip install -r scripts/requirements-kb.txt
```

Для подготовки Experience Cases:

```bash
python -m pip install -r scripts/requirements-experience.txt
```

Проверка:

```bash
python -c 'import fitz, httpx; print("OK")'
```

## 9. Первый запуск Docker

```bash
docker compose config --quiet && echo "COMPOSE OK"
```

```bash
docker compose up -d --build
```

```bash
docker compose ps
```

Ожидаемые сервисы:

```text
pdrd-validation-ai-frontend-1
pdrd-validation-ai-pdf-service-1
pdrd-validation-ai-qdrant-1
```

## 10. Модели Ollama

Проверить:

```bash
docker compose exec ollama ollama list
```

Нужны:

```text
qwen3-vl:8b
qwen3-embedding:4b
```

Скачать при необходимости:

```bash
docker compose exec ollama ollama pull qwen3-vl:8b
```

```bash
docker compose exec ollama ollama pull qwen3-embedding:4b
```

Активные модели:

```bash
docker compose exec ollama ollama ps
```

На GPU с небольшой VRAM допустим смешанный CPU/GPU offload.

## 11. Нормативная база

Исходники:

```text
data/knowledge/normative/source/
```

Текущий индексатор обрабатывает PDF:

```text
*.pdf
```

DOC-файлы текущим индексатором не индексируются.

Проверка:

```bash
find data/knowledge/normative/source   -maxdepth 1   -type f   -print
```

## 12. База Опыта

Структура кейса:

```text
data/knowledge/experience/cases/PROJECT_ID/
├── before/
│   └── before.pdf
├── after/
│   └── after.pdf
├── dxf/
│   ├── before/
│   └── after/
└── annotations/
    ├── issues.json
    └── meta.json
```

Подготовка новых кейсов:

```bash
source .venv/bin/activate
python -m scripts.build_experience_cases
```

Скрипт создаёт:

```text
annotations/issues.json
annotations/meta.json
```

Если в `annotations/` уже есть файлы, подготовленный проект пропускается.

Для повторной подготовки конкретного проекта удалить сгенерированные файлы из его `annotations/` и снова запустить:

```bash
python -m scripts.build_experience_cases
```

## 13. Индексация Qdrant

Индексация выполняется вручную:

```bash
cd ~/PDRD-validation
source .venv/bin/activate
python -m scripts.kb_sync
```

Что делает `kb_sync`:

1. проверяет Ollama и Qdrant;
2. определяет размер embedding;
3. создаёт collections;
4. индексирует нормативные PDF;
5. индексирует Experience Cases;
6. использует SHA-256;
7. пропускает неизменённые источники;
8. обновляет изменённые источники.

Для `qwen3-embedding:4b` ожидается:

```text
Размер вектора: 2560
```

### Проверка incremental sync

Сразу повторить:

```bash
python -m scripts.kb_sync
```

Для неизменённых источников:

```text
[SKIP] Не изменён.
```

## 14. После изменения нормативов

Добавить/изменить PDF в:

```text
data/knowledge/normative/source/
```

Затем:

```bash
source .venv/bin/activate
python -m scripts.kb_sync
```

Новый/изменённый документ будет проиндексирован, остальные пропущены.

## 15. После изменения Experience Case

После изменения `issues.json`, `meta.json`, BEFORE или AFTER:

```bash
source .venv/bin/activate
python -m scripts.kb_sync
```

Ожидается:

```text
[UPDATE] Удаляем старую версию.
```

## 16. Смена embedding-модели

Индексировать одной embedding-моделью, а искать другой нельзя.

При смене модели нужно пересоздать vectors.

Рекомендуемый подход — новая collection:

```dotenv
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:4b
QDRANT_NORMATIVE_COLLECTION=dva_normative_v2
QDRANT_EXPERIENCE_COLLECTION=dva_experience_v2
```

После этого:

```bash
python -m scripts.kb_sync
```

## 17. Проверка Qdrant

```bash
curl -fsS http://localhost:6333/collections   | python -m json.tool
```

Normative:

```bash
curl -fsS   http://localhost:6333/collections/dva_normative_v2   | python -m json.tool
```

Experience:

```bash
curl -fsS   http://localhost:6333/collections/dva_experience_v2   | python -m json.tool
```

Dashboard:

```text
http://localhost:6333/dashboard
```

## 18. Semantic search test

```bash
source .venv/bin/activate
python -m scripts.kb_search
```

## 19. Проверка кода перед rebuild

```bash
source .venv/bin/activate
```

```bash
python -m py_compile   services/pdf-service/app/main.py   services/pdf-service/app/rag.py   services/pdf-service/app/validator.py   scripts/kb_common.py   scripts/kb_sync.py   scripts/kb_search.py
```

```bash
docker compose config --quiet && echo "COMPOSE OK"
```

## 20. Rebuild

Только backend:

```bash
docker compose up -d   --build   --no-deps   pdf-service
```

Backend + frontend:

```bash
docker compose up -d   --build   --no-deps   pdf-service frontend
```

Весь стек:

```bash
docker compose up -d --build
```

## 21. Health checks

Liveness:

```bash
curl -fsS   http://localhost:8101/health/live   | python -m json.tool
```

Readiness с нормальной кириллицей:

```bash
curl -fsS   http://localhost:8101/health/ready   | python -c 'import sys,json; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))'
```

Ожидается:

```text
status: ready
vision_model: qwen3-vl:8b
vision_model_available: true
embedding_model: qwen3-embedding:4b
embedding_model_available: true
collections_ready: true
```

## 22. URL

Frontend:

```text
http://localhost:8080
```

n8n:

```text
http://localhost:5678
```

Qdrant:

```text
http://localhost:6333
```

Qdrant Dashboard:

```text
http://localhost:6333/dashboard
```

PDF-service:

```text
http://localhost:8101
```

FastAPI docs:

```text
http://localhost:8101/docs
```

Ollama:

```text
http://localhost:11434
```

## 23. Первый тест через браузер

Открыть:

```text
http://localhost:8080
```

Далее:

1. выбрать PDF;
2. DXF оставить пустым;
3. указать одну страницу, например `7`;
4. нажать **Анализировать**;
5. дождаться отчёта.

На текущем железе один лист может обрабатываться несколько минут.

Один лист может потребовать:

```text
1. понимание страницы;
2. нормативную проверку;
3. финальное оформление.
```

Плюс embeddings и Qdrant retrieval.

## 24. Тест через API

```bash
curl -fsS   -X POST   http://localhost:8101/analyze   -F "file=@/path/to/project.pdf;type=application/pdf"   -F "pages=7"   > /tmp/analysis.json
```

Вывод кириллицы:

```bash
python - <<'PY'
import json

with open(
    "/tmp/analysis.json",
    encoding="utf-8",
) as file:
    data = json.load(file)

print(
    json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )
)
PY
```

## 25. Формат итогового замечания

Пример:

```json
{
  "finding_id": "p7-f1",
  "page": 7,
  "page_type": "scheme",
  "category": "маркировка",
  "severity": "warning",
  "status": "confirmed",
  "comment": "...",
  "evidence": "...",
  "recommendation": "...",
  "confidence": 0.9,
  "basis": "...",
  "basis_sources": [],
  "experience_sources": []
}
```

`confirmed` — требование применимо и виден конкретный факт, который ему противоречит.

`needs_review` — требование выглядит применимым, но данных PDF недостаточно для уверенного автоматического вывода.

## 26. Как используется нормативная база

VLM не должна самостоятельно вспоминать нормативы.

```text
лист
 ↓
структурированное описание
 ↓
темы проверки
 ↓
embedding
 ↓
Qdrant normative
 ↓
реальные chunks
 ↓
VLM compliance check
```

Модель может ссылаться только на переданные `N1`, `N2`, `N3` и т. д.

Similarity score не является доказательством нарушения.

## 27. Как используется База Опыта

```text
нарушение уже найдено
 ↓
embedding
 ↓
Qdrant experience
 ↓
E1 / E2 / E3
 ↓
VLM
 ↓
формулировка + рекомендация
```

База Опыта не создаёт норматив и не подтверждает нарушение.

## 28. Логи

Все:

```bash
docker compose logs -f
```

PDF-service:

```bash
docker compose logs -f pdf-service
```

Ollama:

```bash
docker compose logs -f ollama
```

n8n:

```bash
docker compose logs -f n8n
```

Qdrant:

```bash
docker compose logs -f qdrant
```

Последние 200 строк backend:

```bash
docker compose logs --tail=200 pdf-service
```

## 29. GPU/CPU

```bash
watch -n 1 nvidia-smi
```

```bash
docker compose exec ollama ollama ps
```

Смешанный CPU/GPU offload допустим для MVP.

## 30. Остановка

```bash
docker compose stop
```

Запуск:

```bash
docker compose start
```

Удаление контейнеров без volumes:

```bash
docker compose down
```

Не использовать без необходимости:

```bash
docker compose down -v
```

Эта команда удаляет persistent volumes PostgreSQL, n8n, Qdrant и Ollama.

## 31. Git workflow

```bash
git status
git diff --stat
```

```bash
git add .
git commit -m "update normative RAG pipeline"
git push
```

На другой машине:

```bash
git pull
```

Qdrant vectors не хранятся в Git.

Если изменились исходники KB, после `git pull` выполнить:

```bash
source .venv/bin/activate
python -m scripts.kb_sync
```

## 32. Когда нужна переиндексация

### Изменился только backend/frontend код

Не нужна.

```bash
docker compose up -d   --build   --no-deps   pdf-service frontend
```

### Изменились нормативы или Experience Cases

Нужна:

```bash
source .venv/bin/activate
python -m scripts.kb_sync
```

### Изменилась embedding-модель

Нужна полная переиндексация в новую collection или после удаления старой.

### Изменилась VLM

Переиндексация Qdrant не нужна.

## 33. Ограничения MVP

### DXF

Пока не участвует.

План:

```text
DXF
 ↓
CAD parser
 ↓
LINE / POLYLINE / TEXT / MTEXT / BLOCK / INSERT
 ↓
слои + координаты + связи
 ↓
граф схемы
 ↓
совместная проверка с PDF
```

Это должно улучшить проверку:

- недочерченных линий;
- отсутствующих соединений;
- разрывов;
- неправильных связей;
- элементов без подключения;
- несоответствия PDF и CAD.

### Нормативная KB

Сейчас используется chunking текста PDF.

Следующий уровень — structured requirements:

```json
{
  "discipline": "АТМ",
  "check_type": "cable_marking",
  "requirement": "...",
  "document": "...",
  "page": 37,
  "clause": "...",
  "applicable_to": [
    "control_cable"
  ]
}
```

### Большие листы

Пока один лист анализируется как одно изображение.

В дальнейшем:

```text
общий preview
+
high-resolution tiles
+
агрегация результатов
```

## 34. План развития

Готово:

- Docker Compose;
- frontend;
- n8n;
- FastAPI PDF-service;
- Ollama;
- Qwen3-VL;
- Qwen3-Embedding;
- Qdrant;
- normative KB;
- Experience KB;
- incremental sync;
- PDF page selection;
- normative RAG pipeline;
- опыт после нормативной проверки.

Далее:

- тестирование на реальных PDF;
- Recall / Precision;
- Excel;
- structured normative requirements;
- reranker;
- DXF parser;
- graph checks;
- tiling;
- annotated PDF;
- более сильная VLM/GPU при необходимости.

## 35. Быстрый запуск с нуля

```bash
cd ~/PDRD-validation
git pull
```

```bash
test -f .env || cp .env.example .env
```

Проверить `.env`.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r scripts/requirements-kb.txt
```

```bash
docker compose up -d --build
```

```bash
docker compose exec ollama ollama pull qwen3-vl:8b
docker compose exec ollama ollama pull qwen3-embedding:4b
```

```bash
python -m scripts.kb_sync
python -m scripts.kb_sync
```

```bash
curl -fsS   http://localhost:8101/health/ready   | python -c 'import sys,json; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))'
```

Открыть:

```text
http://localhost:8080
```

## 36. Обычный запуск

```bash
cd ~/PDRD-validation
docker compose up -d
docker compose ps
```

Frontend:

```text
http://localhost:8080
```

## 37. Обновление кода

```bash
cd ~/PDRD-validation
git pull
git status
```

```bash
docker compose up -d   --build   --no-deps   pdf-service frontend
```

Если KB не менялась, `kb_sync` не нужен.

## 38. Обновление KB

```bash
cd ~/PDRD-validation
source .venv/bin/activate
python -m scripts.kb_sync
```

Второй запуск:

```bash
python -m scripts.kb_sync
```

Неизменённые источники должны быть `[SKIP]`.

## 39. Troubleshooting

### Collections не готовы

```bash
curl -fsS   http://localhost:6333/collections   | python -m json.tool
```

```bash
source .venv/bin/activate
python -m scripts.kb_sync
```

### Не найдена модель

```bash
docker compose exec ollama ollama list
```

### PDF-service не healthy

```bash
docker compose logs --tail=200 pdf-service
```

### n8n перестал открываться после `.env`

Проверить:

```bash
grep '^N8N_ENCRYPTION_KEY=' .env
```

Не менять существующий encryption key без необходимости.

### JSON показывает `\u041...`

Использовать:

```bash
python -c 'import sys,json; print(json.dumps(json.load(sys.stdin), ensure_ascii=False, indent=2))'
```

## 40. Критерии первого реального теста

Оценивать:

1. правильно ли VLM поняла тип листа;
2. корректно ли выделила объекты и связи;
3. релевантны ли retrieved нормативы;
4. применим ли выбранный норматив;
5. соответствует ли `evidence` реальному PDF;
6. нет ли придуманных ГОСТ/СП;
7. правильно ли разделены `confirmed` и `needs_review`;
8. релевантен ли найденный Experience Case;
9. не используется ли Experience как доказательство;
10. практична ли рекомендация.

Для эталонных документов затем считать:

```text
Recall
Precision
False Positive Rate
Recall@K
MRR
```

## 41. Главный принцип

```text
Модель не обязана помнить все нормы.

Она должна:
1. понять инженерный лист;
2. определить, что нужно проверить;
3. получить реальные требования из Qdrant;
4. проверить лист по этим требованиям;
5. сформировать нарушение с доказательством;
6. использовать прошлый экспертный опыт
   только для полезной формулировки
   и рекомендации.
```
