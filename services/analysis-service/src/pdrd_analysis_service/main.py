# services/analysis-service/src/pdrd_analysis_service/main.py

"""FastAPI entry point Analysis Service."""

from fastapi import FastAPI

from pdrd_analysis_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_analysis_service.transport.http.routes import (
    router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт FastAPI application."""
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
        router,
    )

    return application


app = create_app()
