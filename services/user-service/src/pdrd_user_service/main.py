# services/user-service/src/pdrd_user_service/main.py

"""Точка входа внутреннего HTTP-приложения User Service."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from pdrd_user_service.core.container import ApplicationContainer, build_container
from pdrd_user_service.transport.http.routers.admin_users import (
    router as admin_users_router,
)
from pdrd_user_service.transport.http.routers.external_accounts import (
    router as external_accounts_router,
)
from pdrd_user_service.transport.http.routers.health import router as health_router
from pdrd_user_service.transport.http.routers.local_accounts import (
    router as local_accounts_router,
)
from pdrd_user_service.transport.http.routers.organization_memberships import (
    router as organization_memberships_router,
)
from pdrd_user_service.transport.http.routers.review_scope import (
    router as review_scope_router,
)
from pdrd_user_service.transport.http.routers.role_replacement import (
    router as role_replacement_router,
)
from pdrd_user_service.transport.http.routers.users import router as users_router


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    """Собирает HTTP маршруты; закрытый API отсутствует у выключенного сервиса."""
    actual = container if container is not None else build_container()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Закрывает пул БД при остановке приложения."""
        del application
        try:
            yield
        finally:
            await actual.close()

    app = FastAPI(
        title="PDRD User Service",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def prevent_identity_cache(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Не даёт кешировать профиль, роль или версию полномочий."""
        response = await call_next(request)
        if request.url.path.startswith("/internal/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.state.container = actual
    app.include_router(health_router)
    if actual.settings.enabled and actual.settings.internal_key.get_secret_value():
        app.include_router(local_accounts_router)
        app.include_router(users_router)
        app.include_router(external_accounts_router)
        app.include_router(admin_users_router)
        app.include_router(role_replacement_router)
        app.include_router(organization_memberships_router)
        app.include_router(review_scope_router)
    return app


app = create_app()
