# services/user-service/src/pdrd_user_service/core/container.py

"""Создаёт транзакционные адаптеры и закрывает ресурсы User Service."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pdrd_user_service.application.use_cases.external_accounts import ExternalAccounts
from pdrd_user_service.application.use_cases.list_users import AdminUserListing
from pdrd_user_service.application.use_cases.local_superuser import LocalSuperusers
from pdrd_user_service.application.use_cases.organization_memberships import (
    OrganizationMemberships,
)
from pdrd_user_service.application.use_cases.replace_role import ReplaceWorkerRole
from pdrd_user_service.application.use_cases.review_scope import ReviewScopeAccess
from pdrd_user_service.application.use_cases.users import UserDirectory
from pdrd_user_service.core.observability import configure_identity_logging
from pdrd_user_service.core.settings import Settings, get_settings
from pdrd_user_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_user_service.infrastructure.database.health import DatabaseReadinessProbe
from pdrd_user_service.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


class ReadinessProbe(Protocol):
    """Проверяет PostgreSQL и состояние миграций без изменения данных."""

    async def is_ready(self) -> bool:
        """Возвращает готовность хранилища."""
        ...


class DisabledReadinessProbe:
    """Запрещает объявлять выключенный сервис готовым."""

    async def is_ready(self) -> bool:
        """Не обращается к PostgreSQL."""
        return False


ShutdownCallback = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит зависимости одного экземпляра приложения."""

    settings: Settings
    readiness: ReadinessProbe
    directory: UserDirectory | None
    shutdown_callback: ShutdownCallback
    external_accounts: ExternalAccounts | None = None
    user_listing: AdminUserListing | None = None
    role_replacement: ReplaceWorkerRole | None = None
    organization_memberships: OrganizationMemberships | None = None
    review_scope_access: ReviewScopeAccess | None = None
    local_superusers: LocalSuperusers | None = None

    async def close(self) -> None:
        """Освобождает созданный пул соединений."""
        await self.shutdown_callback()


def build_container(settings: Settings | None = None) -> ApplicationContainer:
    """Включает БД и внутренний API лишь при явном разрешении конфигурации."""
    actual_settings = settings if settings is not None else get_settings()

    if not actual_settings.enabled:

        async def close_disabled() -> None:
            """У выключенного сервиса нет соединений для освобождения."""

        return ApplicationContainer(
            settings=actual_settings,
            readiness=DisabledReadinessProbe(),
            directory=None,
            shutdown_callback=close_disabled,
            external_accounts=None,
            user_listing=None,
            role_replacement=None,
            organization_memberships=None,
            review_scope_access=None,
        )

    configure_identity_logging()
    engine = build_async_engine(actual_settings.database)
    session_factory = build_session_factory(engine)

    async def close_database() -> None:
        """Закрывает пул подключения User Service."""
        await engine.dispose()

    def unit_of_work() -> SqlAlchemyUnitOfWork:
        """Создаёт отдельную транзакцию для каждого сценария каталога."""
        return SqlAlchemyUnitOfWork(session_factory)

    return ApplicationContainer(
        settings=actual_settings,
        readiness=DatabaseReadinessProbe(
            engine,
            timeout_seconds=actual_settings.database.health_timeout_seconds,
        ),
        directory=UserDirectory(unit_of_work),
        shutdown_callback=close_database,
        external_accounts=ExternalAccounts(unit_of_work),
        user_listing=AdminUserListing(unit_of_work),
        role_replacement=ReplaceWorkerRole(unit_of_work),
        organization_memberships=OrganizationMemberships(unit_of_work),
        review_scope_access=ReviewScopeAccess(unit_of_work),
        local_superusers=LocalSuperusers(unit_of_work),
    )
