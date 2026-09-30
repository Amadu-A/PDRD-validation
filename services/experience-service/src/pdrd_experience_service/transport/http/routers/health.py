# services/experience-service/src/pdrd_experience_service/transport/http/routers/health.py

"""Системные HTTP endpoints Experience Service.

Назначение:
- /health/live сообщает, что HTTP-процесс работает;
- /health/ready проверяет, можно ли принимать рабочий трафик.

Выключенный Experience Service всегда возвращает
HTTP 503 на /health/ready независимо от состояния PostgreSQL.

Маршруты не предоставляют доступ к Human Review
и не возвращают секреты конфигурации.
"""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_experience_service.core.container import (
    ApplicationContainer,
)
from pdrd_experience_service.transport.http.dependencies import (
    get_container,
)

router = APIRouter(
    tags=["health"],
)


@router.get(
    "/health/live",
)
def health_live(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> dict[str, str]:
    """Сообщает, что HTTP-приложение запущено."""
    return {
        "service": "PDRD Experience Service",
        "environment": container.settings.environment,
        "status": "alive",
    }


@router.get(
    "/health/ready",
)
async def health_ready(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> dict[str, str]:
    """Проверяет готовность включённого сервиса и PostgreSQL."""
    if not container.settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "disabled",
            },
        )

    ready = await container.check_readiness.execute()

    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "database_unavailable",
            },
        )

    return {
        "service": "PDRD Experience Service",
        "status": "ready",
    }
