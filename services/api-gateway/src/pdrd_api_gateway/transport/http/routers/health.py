# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/health.py

"""Системные health endpoints API Gateway.

Liveness подтверждает, что процесс способен отвечать на HTTP-запросы.
Readiness на текущем этапе подтверждает корректную сборку приложения.
После подключения PostgreSQL и RabbitMQ readiness будет дополнен проверкой
обязательных инфраструктурных зависимостей.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.schemas.health import (
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
    """Возвращает liveness сервиса.

    Args:
        container: Runtime dependencies API Gateway.

    Returns:
        Стабильный liveness response.
    """
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
    """Возвращает readiness сервиса.

    Args:
        container: Runtime dependencies API Gateway.

    Returns:
        Текущий readiness response.
    """
    settings = container.settings

    return ReadyHealthResponse(
        service=settings.service_name,
        version=settings.service_version,
        environment=settings.environment,
    )
