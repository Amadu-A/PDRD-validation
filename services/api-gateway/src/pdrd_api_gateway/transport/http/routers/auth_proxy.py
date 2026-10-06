# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/auth_proxy.py

"""Браузерные маршруты входа и профиля через тот же origin API Gateway."""

from fastapi import APIRouter, HTTPException, Request, Response, status

from pdrd_api_gateway.infrastructure.identity_proxy import IdentityProxy
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.identity_proxy import forward_identity_request

router = APIRouter(prefix="/api/v1", tags=["identity-proxy"])


def _proxy(request: Request) -> IdentityProxy:
    """Отклоняет обращение до подключения защищённого контура."""
    proxy = get_container(request).identity_proxy
    if proxy is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервис недоступен.",
            headers={"Cache-Control": "no-store"},
        )
    return proxy


@router.api_route("/auth/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def auth_route(request: Request, path: str) -> Response:
    """Передаёт Auth Service cookie и Origin без служебных заголовков."""
    return await forward_identity_request(
        request, _proxy(request), service="auth", path=f"/api/v1/auth/{path}"
    )


@router.api_route("/users/me", methods=["GET"])
async def my_profile_route(request: Request) -> Response:
    """Читает свой профиль только через действующую сессию Auth Service."""
    return await forward_identity_request(
        request, _proxy(request), service="auth", path="/api/v1/users/me"
    )
