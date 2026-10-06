# services/admin-service/src/pdrd_admin_service/core/container.py

"""Собирает HTTP адаптеры и владеет их жизненным циклом."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from pdrd_admin_service.application.admin_organizations import AdminOrganizations
from pdrd_admin_service.application.admin_users import AdminUsers
from pdrd_admin_service.core.settings import Settings, get_settings
from pdrd_admin_service.infrastructure.clients import (
    AuthServiceClient,
    KnowledgeSectionClient,
    UserServiceClient,
)


@dataclass(slots=True)
class ApplicationContainer:
    """Хранит только зависимости собственного процесса, без подключения к БД."""

    settings: Settings
    admin_users: AdminUsers | None
    readiness: Callable[[], Awaitable[bool]]
    shutdown_callback: Callable[[], Awaitable[None]]
    admin_organizations: AdminOrganizations | None = None

    async def close(self) -> None:
        """Закрывает оба HTTP пула при остановке приложения."""
        await self.shutdown_callback()


def build_container(settings: Settings | None = None) -> ApplicationContainer:
    """Включает рабочие маршруты только после проверки ключей и адресов."""
    actual = settings or get_settings()
    if not actual.enabled:

        async def disabled() -> bool:
            """Отключённый сервис не объявляет себя готовым."""
            return False

        async def noop() -> None:
            """Без клиентов закрывать нечего."""

        return ApplicationContainer(actual, None, disabled, noop)

    timeout = httpx.Timeout(actual.request_timeout_seconds)
    auth_http = httpx.AsyncClient(
        base_url=actual.auth_service_url, timeout=timeout, trust_env=False
    )
    user_http = httpx.AsyncClient(
        base_url=actual.user_service_url, timeout=timeout, trust_env=False
    )
    auth_client = AuthServiceClient(
        auth_http, actual.auth_service_internal_key.get_secret_value()
    )
    user_client = UserServiceClient(
        user_http, actual.user_service_internal_key.get_secret_value()
    )
    knowledge_http = httpx.AsyncClient(
        base_url=actual.knowledge_service_url, timeout=timeout, trust_env=False
    )
    admin_users = AdminUsers(
        auth_client, user_client, KnowledgeSectionClient(knowledge_http)
    )
    admin_organizations = AdminOrganizations(auth_client, user_client)

    async def readiness() -> bool:
        """Подтверждает готовность обоих зависимых сервисов без утечки их URL."""
        try:
            auth_response = await auth_http.get("/health/ready")
            user_response = await user_http.get("/health/ready")
        except httpx.RequestError:
            return False
        return auth_response.status_code == 200 and user_response.status_code == 200

    async def close() -> None:
        """Освобождает ресурсы обоих внутренних клиентов."""
        await auth_http.aclose()
        await user_http.aclose()
        await knowledge_http.aclose()

    return ApplicationContainer(
        actual, admin_users, readiness, close, admin_organizations
    )
