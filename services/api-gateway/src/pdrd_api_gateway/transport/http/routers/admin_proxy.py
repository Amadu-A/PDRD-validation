# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/admin_proxy.py

"""Браузерные маршруты администрирования через отдельный Admin Service."""

from fastapi import APIRouter, HTTPException, Request, Response, status

from pdrd_api_gateway.infrastructure.identity_proxy import IdentityProxy
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.identity_proxy import forward_identity_request

router = APIRouter(prefix="/api/v1/admin", tags=["admin-proxy"])


def _proxy(request: Request) -> IdentityProxy:
    """Не публикует маршруты без включённого защищённого контура."""
    proxy = get_container(request).identity_proxy
    if proxy is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервис недоступен.",
            headers={"Cache-Control": "no-store"},
        )
    return proxy


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def admin_route(request: Request, path: str) -> Response:
    """Перенаправляет запрос без X-PDRD-Actor-Id и внутренних ключей клиента."""
    return await forward_identity_request(
        request, _proxy(request), service="admin", path=f"/api/v1/admin/{path}"
    )
