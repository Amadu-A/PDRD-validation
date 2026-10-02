# services/auth-service/src/pdrd_auth_service/transport/http/routers/internal.py

"""Закрытая проверка сессии для административного сервиса."""

import secrets

from fastapi import APIRouter, Header, HTTPException

from pdrd_auth_service.transport.http.dependencies import Runtime
from pdrd_auth_service.transport.http.schemas import IntrospectRequest
from pdrd_auth_service.transport.http.security import csrf_token

router = APIRouter(prefix="/internal/v1/auth", tags=["internal-auth"])


@router.post("/introspect")
async def introspect(
    command: IntrospectRequest,
    runtime: Runtime,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Доверяет только сервисному ключу и возвращает текущие права из user-service."""
    expected = f"Bearer {runtime.settings.http.internal_key.get_secret_value()}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(403, "Доступ запрещён")
    current = await runtime.current.execute(command.token)
    return {
        "user_id": str(current.profile.user_id),
        "permissions": list(current.permissions.permissions),
        "csrf_token": csrf_token(command.token, runtime.settings.http),
    }
