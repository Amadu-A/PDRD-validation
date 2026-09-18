# services/analysis-service/src/pdrd_analysis_service/main.py

"""FastAPI entry point Analysis Service."""

from fastapi import (
    FastAPI,
    Request,
    status,
)
from starlette.middleware.base import (
    RequestResponseEndpoint,
)
from starlette.responses import (
    JSONResponse,
    Response,
)

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
    vision_model_residency,
)
from pdrd_analysis_service.core.container import (
    ApplicationContainer,
    build_container,
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

_VLM_RESIDENCY_PATHS = frozenset(
    {
        "/internal/v1/pages/understand",
        "/internal/v1/pages/check-norms",
        "/internal/v1/pages/check-technical-assignment",
        "/internal/v1/project-context/validate",
        "/internal/v1/findings/finalize",
        "/internal/v1/findings/localize",
        "/internal/v1/stages/understand-pages",
        "/internal/v1/stages/check-technical-assignment",
        "/internal/v1/stages/check-norms",
        "/internal/v1/stages/finalize",
    }
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

    @application.middleware(
        "http",
    )
    async def vlm_residency_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Удерживает одну VLM на весь bounded GPU stage."""
        if request.url.path not in _VLM_RESIDENCY_PATHS:
            return await call_next(
                request,
            )

        vision_model = application_container.understand_page.vision_model

        try:
            async with vision_model_residency(
                vision_model,
            ):
                return await call_next(
                    request,
                )

        except VisionModelError as error:
            return JSONResponse(
                status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                content={
                    "detail": str(
                        error,
                    ),
                },
            )

    application.include_router(
        router,
    )

    application.include_router(
        project_context_router,
    )

    application.include_router(
        technical_assignment_router,
    )

    application.include_router(
        stage_batch_router,
    )

    return application


app = create_app()
