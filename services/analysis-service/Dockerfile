# services/analysis-service/Dockerfile
# Multi-stage image Analysis Service.

FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

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

CMD ["pytest", "-q", "/app/tests", "--import-mode=importlib"]


FROM base AS runtime

RUN useradd \
    --create-home \
    --uid 1000 \
    app

USER app

EXPOSE 8501

CMD ["uvicorn", "pdrd_analysis_service.main:app", "--host", "0.0.0.0", "--port", "8501"]