# services/api-gateway/src/pdrd_api_gateway/main.py

"""Точка входа FastAPI-приложения API Gateway.

Модуль создаёт HTTP-приложение, подключает composition container и routers.
Бизнес-логика и создание infrastructure adapters непосредственно здесь
не размещаются.
"""

from fastapi import FastAPI

from pdrd_api_gateway.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_api_gateway.transport.http.routers.health import (
    router as health_router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт настроенный экземпляр FastAPI.

    Args:
        container: Необязательный заранее собранный container. Используется
            тестами для явной подстановки конфигурации и зависимостей.

    Returns:
        Готовое FastAPI-приложение.
    """
    application_container = container if container is not None else build_container()

    settings = application_container.settings

    docs_url = "/docs" if settings.docs_enabled else None
    redoc_url = "/redoc" if settings.docs_enabled else None
    openapi_url = "/openapi.json" if settings.docs_enabled else None

    application = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    application.state.container = application_container

    application.include_router(
        health_router,
    )

    return application


app = create_app()
