# services/knowledge-service/src/pdrd_knowledge_service/main.py

"""FastAPI entry point Knowledge Service."""

from fastapi import FastAPI

from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_knowledge_service.transport.http.routers.health import (
    router as health_router,
)
from pdrd_knowledge_service.transport.http.routers.normative_sections import (
    router as normative_sections_router,
)
from pdrd_knowledge_service.transport.http.routers.project_context import (
    router as project_context_router,
)
from pdrd_knowledge_service.transport.http.routers.search import (
    router as search_router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт настроенное FastAPI-приложение."""
    application_container = container if container is not None else build_container()

    settings = application_container.settings

    application = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        docs_url=("/docs" if settings.docs_enabled else None),
        redoc_url=("/redoc" if settings.docs_enabled else None),
        openapi_url=("/openapi.json" if settings.docs_enabled else None),
    )

    application.state.container = application_container

    application.include_router(
        health_router,
    )

    application.include_router(
        search_router,
    )

    application.include_router(
        project_context_router,
    )

    application.include_router(
        normative_sections_router,
    )

    return application


app = create_app()
