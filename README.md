# Drawing Validation AI — Stage 1

На этом этапе проверяем инфраструктуру и путь `Browser -> Frontend -> n8n`.

## 1. Включить WSL Integration

В Docker Desktop:

```text
Settings -> Resources -> WSL Integration -> Ubuntu = ON -> Apply & restart
```

Затем в Ubuntu:

```bash
docker version
docker compose version
```

## 2. Проверить GPU внутри Docker

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.8.0-base-ubuntu24.04 \
  nvidia-smi
```

## 3. Подготовить проект

```bash
mkdir -p ~/projects
cd ~/projects
```

Распаковать архив в `~/projects/`, затем:

```bash
cd ~/projects/drawing-validation-ai-stage1
cp .env.example .env
```

Сгенерировать ключ:

```bash
openssl rand -hex 32
```

Подставить его в `N8N_ENCRYPTION_KEY`, а также поменять `POSTGRES_PASSWORD`.

## 4. Запуск

```bash
docker compose pull
docker compose up -d --build
docker compose ps
```

Адреса:

```text
Frontend: http://localhost:8080
n8n:      http://localhost:5678
Qdrant:   http://localhost:6333/dashboard
Ollama:   http://localhost:11434
```

## 5. Импорт workflow

В n8n создать локального owner-пользователя, затем:

```text
Workflows -> Import from File
```

Импортировать:

```text
n8n/workflows/analysis-smoke.json
```

После импорта активировать workflow.

## 6. Проверка frontend

Открыть `http://localhost:8080`, выбрать PDF, при желании DXF,
ввести `3,5,8-12` и нажать «Анализировать».

Ожидается JSON со `status: accepted`.

## 7. Модель для текущего ПК

GTX 1050 Ti не подходит для GPU-vLLM, поэтому тестовый MVP использует
Ollama в Docker и `qwen3-vl:2b`.

Загрузить модель:

```bash
docker compose exec ollama ollama pull qwen3-vl:2b
docker compose exec ollama ollama list
```

## 8. Проверка стека

```bash
chmod +x scripts/check-stack.sh
./scripts/check-stack.sh
```

## Каталоги базы знаний

Нормативы:

```text
data/knowledge/normative/source/
```

База опыта:

```text
data/knowledge/experience/cases/
```

На следующем этапе подключаем реальный `pdf-service`, разбор диапазонов
страниц, автоматическую классификацию страниц и первый запрос изображения
чертежа в Qwen3-VL.
