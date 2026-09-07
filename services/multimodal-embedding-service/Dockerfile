# services/multimodal-embedding-service/Dockerfile
# Multi-stage image multimodal embedding service.

FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=1000
ENV PIP_RETRIES=10

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install \
    --no-cache-dir \
    .

FROM base AS test

RUN pip install \
    --no-cache-dir \
    ".[test]"

COPY tests /app/tests

CMD ["pytest", "-q", "/app/tests"]

FROM base AS runtime

RUN pip install \
    --no-cache-dir \
    --timeout 1000 \
    --retries 10 \
    --index-url https://download.pytorch.org/whl/cu128 \
    torch==2.8.0 \
    torchvision==0.23.0

RUN pip install \
    --no-cache-dir \
    --timeout 1000 \
    --retries 10 \
    ".[gpu]"

RUN useradd \
    --create-home \
    --uid 1000 \
    app \
    && mkdir -p /models/huggingface \
    && chown -R app:app /models

ENV HOME=/home/app
ENV HF_HOME=/models/huggingface
ENV TOKENIZERS_PARALLELISM=false
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

USER app

EXPOSE 8601

CMD ["uvicorn", "pdrd_multimodal_embedding_service.main:app", "--host", "0.0.0.0", "--port", "8601"]