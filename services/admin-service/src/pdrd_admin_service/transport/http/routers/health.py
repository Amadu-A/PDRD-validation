# services/admin-service/src/pdrd_admin_service/transport/http/routers/health.py

"""Отделяет живой процесс от готовности зависимых сервисов."""

from fastapi import APIRouter, HTTPException, status

from pdrd_admin_service.transport.http.dependencies import Container

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    """Подтверждает, что HTTP процесс запущен."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(container: Container) -> dict[str, str]:
    """Проверяет доступность auth и user перед приёмом админ-запросов."""
    if not await container.readiness():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис недоступен.")
    return {"status": "ready"}
