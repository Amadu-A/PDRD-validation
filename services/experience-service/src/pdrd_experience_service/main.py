# services/experience-service/src/pdrd_experience_service/main.py

"""Точка входа HTTP-приложения Experience Service.

Назначение файла:
- создавать экземпляр FastAPI;
- подключать маршруты проверки состояния;
- внедрять общий ApplicationContainer;
- корректно закрывать инфраструктурные ресурсы.

Бизнес-маршруты закрыты по умолчанию и включаются только для API Gateway.
Готовность проверяет проектный PostgreSQL, а не доступность фронта.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pdrd_experience_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_experience_service.core.observability import configure_catalog_logging
from pdrd_experience_service.transport.http.routers.artifacts import (
    router as artifacts_router,
)
from pdrd_experience_service.transport.http.routers.catalog import (
    router as catalog_router,
)
from pdrd_experience_service.transport.http.routers.health import (
    router as health_router,
)
from pdrd_experience_service.transport.http.routers.index_feed import (
    router as index_router,
)
from pdrd_experience_service.transport.http.routers.review import (
    router as review_router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт HTTP-приложение из явно переданного контейнера.

    Тесты могут подменять зависимости без запуска PostgreSQL.
    Production использует build_container и общие Settings.
    """
    application_container = container if container is not None else build_container()

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        """Освобождает ресурсы при завершении работы приложения."""
        del application

        try:
            yield
        finally:
            await application_container.close()

    application = FastAPI(
        title="PDRD Experience Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    application.state.container = application_container

    application.include_router(
        health_router,
    )

    if application_container.settings.review_api_enabled:
        configure_catalog_logging()
        application.include_router(review_router)
        application.include_router(catalog_router)
        application.include_router(artifacts_router)

    if (
        application_container.settings.enabled
        and application_container.settings.index_key.get_secret_value()
    ):
        application.include_router(index_router)

    return application


app = create_app()
