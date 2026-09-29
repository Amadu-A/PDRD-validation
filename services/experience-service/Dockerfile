# services/experience-service/Dockerfile
# Самостоятельный Experience Service: закрытый HTTP API и миграции своей схемы.

FROM python:3.12.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
# Миграции поставляются отдельно от установленного wheel в site-packages.
ENV EXPERIENCE_SERVICE_MIGRATION_DIRECTORY=/app/alembic
WORKDIR /app
COPY pyproject.toml alembic.ini /app/
COPY alembic /app/alembic
COPY src /app/src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data/experience/crops \
    && chown -R app:app /data/experience
USER app
EXPOSE 8000
CMD ["uvicorn", "pdrd_experience_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
