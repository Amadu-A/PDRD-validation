# services/auth-service/src/pdrd_auth_service/main.py

"""HTTP-приложение Auth Service: браузерные cookie и закрытая проверка сессии."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from pdrd_auth_service.application.ports.credentials import (
    CorporateDirectoryUnavailable,
)
from pdrd_auth_service.application.use_cases.browser_login import (
    CorporateLoginUnavailable,
)
from pdrd_auth_service.application.use_cases.sessions import UserStateUnavailable
from pdrd_auth_service.core.runtime import AuthRuntime, build_runtime
from pdrd_auth_service.core.settings import get_settings
from pdrd_auth_service.infrastructure.smtp import VerificationDeliveryUnavailable
from pdrd_auth_service.infrastructure.user_service import UserServiceUnavailable
from pdrd_auth_service.transport.http.routers.browser import router as browser_router
from pdrd_auth_service.transport.http.routers.browser import user_router
from pdrd_auth_service.transport.http.routers.internal import router as internal_router


def create_app(runtime: AuthRuntime | None = None) -> FastAPI:
    """Собирает маршруты без соединения с БД при выключенном HTTP или импорте."""
    actual = runtime
    if actual is None:
        settings = get_settings()
        if settings.http.enabled:
            actual = build_runtime(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Закрывает пулы БД и user-service после остановки HTTP-сервера."""
        try:
            yield
        finally:
            if actual is not None:
                await actual.close()

    app = FastAPI(
        title="PDRD Auth Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = actual

    @app.middleware("http")
    async def no_identity_cache(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Не позволяет прокси или браузеру сохранить личные данные и cookie."""
        response = await call_next(request)
        if request.url.path.startswith(
            ("/api/v1/auth/", "/api/v1/users/", "/internal/v1/auth/")
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(PermissionError)
    async def invalid_identity(_: Request, __: PermissionError) -> JSONResponse:
        """Не раскрывает наличие email, учётной записи AD или причину отказа."""
        return JSONResponse(
            status_code=401, content={"detail": "Неверные учётные данные или сессия"}
        )

    @app.exception_handler(CorporateLoginUnavailable)
    @app.exception_handler(CorporateDirectoryUnavailable)
    @app.exception_handler(UserStateUnavailable)
    @app.exception_handler(UserServiceUnavailable)
    @app.exception_handler(VerificationDeliveryUnavailable)
    @app.exception_handler(SQLAlchemyError)
    async def dependency_unavailable(_: Request, __: Exception) -> JSONResponse:
        """Закрывает вход при сбое БД, LDAP, SMTP или каталога без утечки деталей."""
        return JSONResponse(
            status_code=503, content={"detail": "Сервис временно недоступен"}
        )

    @app.exception_handler(ValueError)
    async def invalid_input(_: Request, error: ValueError) -> JSONResponse:
        """Показывает безопасное сообщение о проверке входных полей."""
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Показывает, что процесс принимает запросы."""
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        """Не отмечает отключённую HTTP-границу готовой к входу."""
        if actual is None:
            return JSONResponse(status_code=503, content={"status": "disabled"})
        return JSONResponse(content={"status": "ready"})

    app.include_router(browser_router)
    app.include_router(user_router)
    app.include_router(internal_router)
    return app


app = create_app()
