# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/health.py

"""Системные health endpoints API Gateway."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.schemas.health import (
    DependenciesHealthResponse,
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
    """Возвращает liveness процесса API Gateway."""
    settings = container.settings

    return LiveHealthResponse(
        service=settings.service_name,
        version=settings.service_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadyHealthResponse,
)
async def health_ready(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> ReadyHealthResponse:
    """Проверяет готовность API Gateway принимать рабочий трафик."""
    readiness = await container.check_readiness.execute()

    if not readiness.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "database": ("ok" if readiness.database_ready else "unavailable"),
                "broker": ("ok" if readiness.broker_ready else "unavailable"),
            },
        )

    settings = container.settings

    return ReadyHealthResponse(
        service=settings.service_name,
        version=settings.service_version,
        environment=settings.environment,
        dependencies=DependenciesHealthResponse(),
    )
