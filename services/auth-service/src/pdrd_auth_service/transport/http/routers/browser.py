# services/auth-service/src/pdrd_auth_service/transport/http/routers/browser.py

"""Вход, подтверждение email и управление собственными сессиями браузера."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from pdrd_auth_service.application.use_cases.current_user import CurrentUser
from pdrd_auth_service.transport.http.dependencies import Runtime
from pdrd_auth_service.transport.http.schemas import (
    LoginRequest,
    RegisterRequest,
    VerifyEmailRequest,
)
from pdrd_auth_service.transport.http.security import (
    browser_token,
    clear_session_cookie,
    client_ip,
    csrf_token,
    require_csrf,
    require_origin,
    set_session_cookie,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
user_router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _public_user(current: CurrentUser) -> dict[str, object]:
    """Публикует только поля профиля и вычисленные полномочия."""
    return {
        "user_id": str(current.profile.user_id),
        "kind": current.profile.kind,
        "tier": current.profile.tier,
        "status": current.profile.status,
        "display_name": current.profile.display_name,
        "login": current.profile.login,
        "email": current.profile.email,
        "roles": list(current.permissions.roles),
        "permissions": list(current.permissions.permissions),
    }


def _session(current: CurrentUser) -> dict[str, object]:
    """Скрывает token_hash и внутреннюю версию полномочий."""
    session = current.session
    return {
        "session_id": str(session.session_id),
        "created_at": session.created_at.isoformat(),
        "last_seen_at": session.last_seen_at.isoformat(),
        "idle_expires_at": session.idle_expires_at.isoformat(),
        "absolute_expires_at": session.absolute_expires_at.isoformat(),
    }


async def _current(request: Request, runtime: Runtime) -> tuple[str, CurrentUser]:
    """Разрешает cookie и проверяет актуальность статуса и роли в user-service."""
    token = browser_token(request)
    if not token:
        raise HTTPException(401, "Требуется вход")
    return token, await runtime.current.execute(token)


async def _limit(
    request: Request,
    runtime: Runtime,
    *,
    namespace: str,
    subject: str,
    limit: int,
    window: timedelta,
) -> None:
    """Ограничивает попытки в общей БД по адресу клиента и идентификатору."""
    allowed = await runtime.limiter.allow(
        namespace=namespace,
        subject=subject,
        limit=limit,
        window=window,
        now=datetime.now(UTC),
    )
    if not allowed:
        raise HTTPException(
            429,
            "Слишком много попыток. Повторите позже",
            headers={"Retry-After": "3600"},
        )


@router.get("/session")
async def session(request: Request, runtime: Runtime) -> dict[str, object]:
    """Отдаёт гостевой ответ без ошибки и свежий снимок активной сессии."""
    token = browser_token(request)
    if not token:
        return {"authenticated": False, "user": None, "session": None}
    try:
        current = await runtime.current.execute(token)
    except PermissionError:
        return {"authenticated": False, "user": None, "session": None}
    return {
        "authenticated": True,
        "user": _public_user(current),
        "session": _session(current),
        "csrf_token": csrf_token(token, runtime.settings.http),
    }


@user_router.get("/me")
async def me(request: Request, runtime: Runtime) -> dict[str, object]:
    """Возвращает профиль только владельцу действующей сессии."""
    _, current = await _current(request, runtime)
    return _public_user(current)


@router.post("/register", status_code=202)
async def register(
    command: RegisterRequest, request: Request, runtime: Runtime
) -> dict[str, str]:
    """Скрывает наличие email и выдаёт одинаковый ответ для повторной заявки."""
    require_origin(request, runtime.settings.http)
    if not runtime.settings.email.enabled:
        raise HTTPException(503, "Регистрация пока не настроена")
    await _limit(
        request,
        runtime,
        namespace="register-ip",
        subject=client_ip(request),
        limit=10,
        window=timedelta(hours=1),
    )
    await _limit(
        request,
        runtime,
        namespace="register-email",
        subject=command.email.strip().lower(),
        limit=3,
        window=timedelta(hours=1),
    )
    await runtime.external.register(
        display_name=command.display_name,
        email=command.email,
        password=command.password,
    )
    return {"message": "Если адрес доступен, письмо для подтверждения отправлено"}


@router.post("/verify-email")
async def verify_email(
    command: VerifyEmailRequest, request: Request, runtime: Runtime
) -> dict[str, str]:
    """Погашает код после активации профиля в user-service."""
    require_origin(request, runtime.settings.http)
    await _limit(
        request,
        runtime,
        namespace="verify-ip",
        subject=client_ip(request),
        limit=20,
        window=timedelta(hours=1),
    )
    await runtime.external.verify_email(command.token)
    return {"message": "Email подтверждён"}


@router.post("/login")
async def login(
    command: LoginRequest, request: Request, response: Response, runtime: Runtime
) -> dict[str, object]:
    """Проверяет источник личности и выдаёт только закрытую cookie."""
    require_origin(request, runtime.settings.http)
    await _limit(
        request,
        runtime,
        namespace="login-ip",
        subject=client_ip(request),
        limit=30,
        window=timedelta(hours=1),
    )
    await _limit(
        request,
        runtime,
        namespace="login-account",
        subject=command.login.strip().lower(),
        limit=10,
        window=timedelta(hours=1),
    )
    issued = await runtime.login.execute(login=command.login, password=command.password)
    set_session_cookie(
        response,
        issued.token,
        runtime.settings.http,
        runtime.settings.sessions.absolute_timeout_seconds,
    )
    return {"authenticated": True}


@router.post("/logout")
async def logout(
    request: Request, response: Response, runtime: Runtime
) -> dict[str, str]:
    """Завершает текущую сессию и удаляет cookie с теми же атрибутами."""
    require_origin(request, runtime.settings.http)
    token = browser_token(request)
    if not token:
        clear_session_cookie(response, runtime.settings.http)
        return {"message": "Выход выполнен"}
    try:
        await runtime.current.execute(token)
    except PermissionError:
        # Истёкшая cookie не должна навсегда блокировать гостевой анализ.
        clear_session_cookie(response, runtime.settings.http)
        return {"message": "Выход выполнен"}
    require_csrf(request, token, runtime.settings.http)
    await runtime.sessions.revoke_current(token)
    clear_session_cookie(response, runtime.settings.http)
    return {"message": "Выход выполнен"}


@router.post("/logout-all")
async def logout_all(
    request: Request, response: Response, runtime: Runtime
) -> dict[str, str]:
    """Отзывает все сессии пользователя на сервере."""
    require_origin(request, runtime.settings.http)
    token, current = await _current(request, runtime)
    require_csrf(request, token, runtime.settings.http)
    await runtime.sessions.revoke_all(current.profile.user_id)
    clear_session_cookie(response, runtime.settings.http)
    return {"message": "Все сессии завершены"}


@router.get("/sessions")
async def sessions(request: Request, runtime: Runtime) -> dict[str, object]:
    """Перечисляет действующие устройства владельца без секретов."""
    _, current = await _current(request, runtime)
    items = await runtime.sessions.list_user(current.profile.user_id)
    return {
        "sessions": [
            {
                "session_id": str(item.session_id),
                "created_at": item.created_at.isoformat(),
                "absolute_expires_at": item.absolute_expires_at.isoformat(),
            }
            for item in items
        ]
    }


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: UUID, request: Request, response: Response, runtime: Runtime
) -> None:
    """Завершает только собственное устройство; при текущем удаляет cookie."""
    require_origin(request, runtime.settings.http)
    token, current = await _current(request, runtime)
    require_csrf(request, token, runtime.settings.http)
    if not await runtime.sessions.revoke_owned(current.profile.user_id, session_id):
        raise HTTPException(404, "Сессия не найдена")
    if current.session.session_id == session_id:
        clear_session_cookie(response, runtime.settings.http)
