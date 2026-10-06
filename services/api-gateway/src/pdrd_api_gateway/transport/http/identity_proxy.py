# services/api-gateway/src/pdrd_api_gateway/transport/http/identity_proxy.py

"""Безопасный перевод ответа закрытого сервиса в одноимённый HTTP-ответ."""

import secrets
from ipaddress import ip_address

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.infrastructure.identity_proxy import (
    IdentityProxy,
    ProxyReply,
    ProxyUnavailable,
    ServiceName,
)

MAX_IDENTITY_BODY_BYTES = 64 * 1024


def _client_ip(request: Request, settings: IdentityProxySettings) -> str:
    """Доверяет адресу nginx только при отдельном серверном ключе proxy."""
    key = settings.trusted_proxy_key.get_secret_value()
    provided = request.headers.get("x-pdrd-proxy-key", "")
    if key and secrets.compare_digest(provided, key):
        try:
            return str(ip_address(request.headers.get("x-real-ip", "")))
        except ValueError:
            pass
    return request.client.host if request.client is not None else "unknown"


async def _read_limited_body(request: Request) -> bytes:
    """Ограничивает небольшие запросы входа, не затрагивая анализ документов."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IDENTITY_BODY_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "Слишком большой запрос.",
                headers={"Cache-Control": "no-store"},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _browser_response(reply: ProxyReply) -> Response:
    """Сохраняет отдельные Set-Cookie и запрещает кеширование личности."""
    response = Response(content=reply.content, status_code=reply.status_code)
    if reply.content_type is not None:
        response.headers["Content-Type"] = reply.content_type
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    if reply.retry_after is not None:
        response.headers["Retry-After"] = reply.retry_after
    for cookie in reply.set_cookies:
        response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
    return response


async def forward_identity_request(
    request: Request,
    proxy: IdentityProxy,
    *,
    service: ServiceName,
    path: str,
) -> Response:
    """Пересылает ограниченное тело и возвращает общий ответ при сбое сервиса."""
    body = await _read_limited_body(request)
    try:
        reply = await proxy.forward(
            service=service,
            method=request.method,
            path=path,
            query=request.url.query,
            headers=request.headers,
            body=body,
            client_ip=_client_ip(
                request, request.app.state.container.settings.identity_proxy
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Маршрут не найден.",
            headers={"Cache-Control": "no-store"},
        ) from error
    except ProxyUnavailable:
        return JSONResponse(
            content={"detail": "Сервис временно недоступен."},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            },
        )
    return _browser_response(reply)
