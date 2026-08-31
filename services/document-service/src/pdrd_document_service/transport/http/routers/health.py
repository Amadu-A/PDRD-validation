# services/document-service/src/pdrd_document_service/transport/http/routers/health.py

"""Health endpoints Document Service."""

from typing import Annotated

from fastapi import APIRouter, Depends

from pdrd_document_service.core.container import (
    ApplicationContainer,
)
from pdrd_document_service.transport.http.dependencies import (
    get_container,
)
from pdrd_document_service.transport.http.schemas.health import (
    LiveHealthResponse,
    ReadyHealthResponse,
)

router = APIRouter(
    tags=["health"],
)


@router.get(
    "/health/live",
    response_model=LiveHealthResponse,
)
def health_live(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> LiveHealthResponse:
    """Возвращает liveness процесса."""
    settings = container.settings

    return LiveHealthResponse(
        service=settings.service_name,
        version=settings.service_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadyHealthResponse,
)
def health_ready(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> ReadyHealthResponse:
    """Возвращает готовность локальных document capabilities."""
    settings = container.settings

    return ReadyHealthResponse(
        service=settings.service_name,
        version=settings.service_version,
        capabilities={
            "pdf": True,
            "dxf": False,
            "dwg": False,
        },
    )
