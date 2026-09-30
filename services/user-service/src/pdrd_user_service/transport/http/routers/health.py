# services/user-service/src/pdrd_user_service/transport/http/routers/health.py

"""Проверки живости процесса и реальной готовности PostgreSQL."""

from fastapi import APIRouter, HTTPException, status

from pdrd_user_service.transport.http.dependencies import Container

router = APIRouter(tags=["health"])


@router.get("/health/live")
def health_live() -> dict[str, str]:
    """Подтверждает работу HTTP-процесса без обращения к БД."""
    return {"service": "PDRD User Service", "status": "alive"}


@router.get("/health/ready")
async def health_ready(container: Container) -> dict[str, str]:
    """Возвращает готовность лишь при включённом сервисе и доступной БД."""
    if (
        not container.settings.enabled
        or not container.settings.internal_key.get_secret_value()
    ):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис выключен.")
    if not await container.readiness.is_ready():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "БД недоступна.")
    return {"service": "PDRD User Service", "status": "ready"}
