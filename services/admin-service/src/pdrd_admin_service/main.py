# services/admin-service/src/pdrd_admin_service/main.py

"""Создаёт внутренний административный HTTP сервис без доступа к SQL."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from pdrd_admin_service.application.admin_users import (
    AuthenticationRequired,
    CsrfRejected,
    InvalidRoleRequest,
    PermissionDenied,
    RoleConflict,
    TargetNotFound,
    UpstreamUnavailable,
)
from pdrd_admin_service.core.container import ApplicationContainer, build_container
from pdrd_admin_service.transport.http.routers.health import router as health_router
from pdrd_admin_service.transport.http.routers.organizations import (
    router as organizations_router,
)
from pdrd_admin_service.transport.http.routers.users import router as users_router


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    """Включает административные маршруты только у настроенного процесса."""
    actual = container if container is not None else build_container()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Закрывает внутренние HTTP клиенты после остановки."""
        del application
        try:
            yield
        finally:
            await actual.close()

    app = FastAPI(
        title="PDRD Admin Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def no_admin_cache(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Не кеширует профиль, права и результат проверки сессии."""
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/admin/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AuthenticationRequired)
    async def auth_error(_: Request, __: AuthenticationRequired) -> JSONResponse:
        """Скрывает сведения о внутренней проверке cookie."""
        return JSONResponse(status_code=401, content={"detail": "Требуется вход."})

    @app.exception_handler(PermissionDenied)
    @app.exception_handler(CsrfRejected)
    async def access_error(_: Request, __: Exception) -> JSONResponse:
        """Возвращает одинаковый код для недоступного действия."""
        return JSONResponse(status_code=403, content={"detail": "Недостаточно прав."})

    @app.exception_handler(TargetNotFound)
    async def missing_error(_: Request, __: TargetNotFound) -> JSONResponse:
        """Не выдаёт внутренние адреса user-service."""
        return JSONResponse(status_code=404, content={"detail": "Не найдено."})

    @app.exception_handler(RoleConflict)
    async def conflict_error(_: Request, __: RoleConflict) -> JSONResponse:
        """Сообщает о конфликте назначения без подробностей БД."""
        return JSONResponse(status_code=409, content={"detail": "Конфликт роли."})

    @app.exception_handler(InvalidRoleRequest)
    async def invalid_role_error(_: Request, __: InvalidRoleRequest) -> JSONResponse:
        """Не выдаёт клиенту внутренний текст ошибок соседнего сервиса."""
        return JSONResponse(status_code=422, content={"detail": "Недопустимая роль."})

    @app.exception_handler(UpstreamUnavailable)
    async def upstream_error(_: Request, __: UpstreamUnavailable) -> JSONResponse:
        """Закрывает доступ при недоступности auth или user."""
        return JSONResponse(status_code=503, content={"detail": "Сервис недоступен."})

    app.state.container = actual
    app.include_router(health_router)
    if actual.settings.enabled and actual.admin_users is not None:
        app.include_router(users_router)
        if actual.admin_organizations is not None:
            app.include_router(organizations_router)
    return app


app = create_app()
