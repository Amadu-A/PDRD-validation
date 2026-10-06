# services/admin-service/Dockerfile
# Изолированный HTTP процесс admin-service; миграций и доступа к PostgreSQL нет.

FROM python:3.12.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 app
USER app
EXPOSE 8000
CMD ["uvicorn", "pdrd_admin_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
