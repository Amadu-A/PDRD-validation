# services/knowledge-service/Dockerfile
# Multi-stage image Knowledge Service.

FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
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

RUN useradd \
    --create-home \
    --uid 1000 \
    app \
    && mkdir -p /data/normative \
    && chown -R app:app /data/normative

USER app

EXPOSE 8401

CMD ["uvicorn", "pdrd_knowledge_service.main:app", "--host", "0.0.0.0", "--port", "8401"]