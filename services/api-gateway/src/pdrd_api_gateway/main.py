# services/api-gateway/src/pdrd_api_gateway/main.py

"""Точка входа FastAPI-приложения API Gateway."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pdrd_api_gateway.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_api_gateway.transport.http.routers.analyses import (
    router as analyses_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_artifacts import (
    router as analysis_artifacts_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_cancellation import (
    router as analysis_cancellation_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_pdf_exports import (
    router as analysis_pdf_exports_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_progress import (
    router as analysis_progress_router,
)
from pdrd_api_gateway.transport.http.routers.health import (
    router as health_router,
)
from pdrd_api_gateway.transport.http.routers.normative_catalog import (
    router as normative_catalog_router,
)
from pdrd_api_gateway.transport.http.routers.project_context_preflight import (
    router as project_context_preflight_router,
)
from pdrd_api_gateway.transport.http.routers.technical_assignments import (
    router as technical_assignments_router,
)
from pdrd_api_gateway.transport.http.routers.user_packages import (
    router as user_packages_router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт настроенный экземпляр FastAPI."""
    application_container = container if container is not None else build_container()

    settings = application_container.settings

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        """Управляет lifecycle infrastructure resources."""
        del application

        try:
            yield

        finally:
            await application_container.close()

    docs_url = "/docs" if settings.docs_enabled else None

    redoc_url = "/redoc" if settings.docs_enabled else None

    openapi_url = "/openapi.json" if settings.docs_enabled else None

    application = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    application.state.container = application_container

    application.include_router(
        health_router,
    )

    application.include_router(
        analyses_router,
    )

    application.include_router(
        analysis_cancellation_router,
    )

    application.include_router(
        analysis_progress_router,
    )

    application.include_router(
        analysis_artifacts_router,
    )

    application.include_router(
        analysis_pdf_exports_router,
    )

    application.include_router(
        project_context_preflight_router,
    )

    application.include_router(
        normative_catalog_router,
    )

    application.include_router(
        user_packages_router,
    )

    application.include_router(
        technical_assignments_router,
    )

    return application


app = create_app()
