# services/auth-service/src/pdrd_auth_service/transport/http/security.py

"""Origin, CSRF и cookie-атрибуты публичной HTTP-границы Auth Service."""

import hashlib
import hmac
from ipaddress import ip_address

from fastapi import HTTPException, Request, Response, status

from pdrd_auth_service.core.settings import HttpSettings

COOKIE_NAME = "pdrd_session"


def browser_token(request: Request) -> str | None:
    """Берёт секрет только из HttpOnly cookie, не из параметра URL."""
    return request.cookies.get(COOKIE_NAME)


def require_origin(request: Request, settings: HttpSettings) -> None:
    """Отклоняет cross-site POST/PATCH/DELETE, включая запросы без Origin."""
    origin = request.headers.get("origin")
    if origin != settings.public_origin.rstrip("/"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недопустимый источник запроса")


def csrf_token(token: str, settings: HttpSettings) -> str:
    """Создаёт стабильный MAC для одной сессии без второго cookie."""
    return hmac.new(
        settings.csrf_key.get_secret_value().encode(),
        f"pdrd-csrf:{token}".encode(),
        hashlib.sha256,
    ).hexdigest()


def require_csrf(request: Request, token: str, settings: HttpSettings) -> None:
    """Сравнивает заголовок с MAC без зависящего от префикса выхода."""
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied, csrf_token(token, settings)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF проверка не пройдена")


def set_session_cookie(
    response: Response, token: str, settings: HttpSettings, absolute_seconds: int
) -> None:
    """Передаёт серверный секрет только через закрытый cookie с общим пределом."""
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=absolute_seconds,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: HttpSettings) -> None:
    """Удаляет cookie с теми же Path и Secure параметрами."""
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def client_ip(request: Request) -> str:
    """Использует IP, переписанный доверенным Gateway, для лимита попыток."""
    forwarded = request.headers.get("x-pdrd-client-ip", "")
    try:
        return str(ip_address(forwarded))
    except ValueError:
        return request.client.host if request.client is not None else "unknown"
