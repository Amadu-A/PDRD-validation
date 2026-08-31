# services/document-service/src/pdrd_document_service/main.py

"""FastAPI entry point Document Service."""

from fastapi import FastAPI

from pdrd_document_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_document_service.transport.http.routers.cad import (
    router as cad_router,
)
from pdrd_document_service.transport.http.routers.health import (
    router as health_router,
)
from pdrd_document_service.transport.http.routers.pdf import (
    router as pdf_router,
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
        pdf_router,
    )

    application.include_router(
        cad_router,
    )

    return application


app = create_app()
