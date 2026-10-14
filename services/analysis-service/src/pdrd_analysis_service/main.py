# services/analysis-service/src/pdrd_analysis_service/main.py

"""Точка входа FastAPI сервиса анализа."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pdrd_analysis_service.application.vision_execution import AnalysisCancelledError
from pdrd_analysis_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_analysis_service.transport.http.document_context_routes import (
    router as document_context_router,
)
from pdrd_analysis_service.transport.http.project_context_routes import (
    router as project_context_router,
)
from pdrd_analysis_service.transport.http.routes import (
    router,
)
from pdrd_analysis_service.transport.http.stage_batch_routes import (
    router as stage_batch_router,
)
from pdrd_analysis_service.transport.http.technical_assignment_routes import (
    router as technical_assignment_router,
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

    @application.exception_handler(AnalysisCancelledError)
    async def cancelled_response(
        request: Request, error: AnalysisCancelledError
    ) -> JSONResponse:
        """Завершает штатную отмену page-вызова без ложной ошибки HTTP-узла n8n."""
        return JSONResponse({"cancelled": True, "reason": "analysis_cancelled"})

    application.include_router(
        router,
    )

    application.include_router(
        project_context_router,
    )

    application.include_router(document_context_router)

    application.include_router(
        technical_assignment_router,
    )

    application.include_router(
        stage_batch_router,
    )

    return application


app = create_app()
