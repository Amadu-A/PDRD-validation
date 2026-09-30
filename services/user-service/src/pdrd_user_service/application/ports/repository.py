# services/user-service/src/pdrd_user_service/application/ports/repository.py

"""Асинхронные порты транзакционного хранилища пользователей."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pdrd_user_service.domain.identity import ExternalIdentity, Membership, UserAccount
from pdrd_user_service.domain.role_assignments import RoleAssignment


class IdentityConflict(Exception):
    """Устойчивая внешняя идентичность уже привязана к другому профилю."""


class AuthorizationConflict(Exception):
    """Версия полномочий изменилась до фиксации назначения роли."""


class BootstrapAlreadyPerformed(Exception):
    """Первичный администратор платформы уже был назначен."""


class UserRepository(Protocol):
    """Читает и изменяет профиль в границах одной транзакции."""

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Читает пользователя; для изменения может блокировать строку."""
        ...

    async def find_identity(
        self, provider_id: str, namespace: str, subject: str
    ) -> UserAccount | None:
        """Ищет профиль только по устойчивому составному ключу."""
        ...

    async def create_user(self, user: UserAccount, identity: ExternalIdentity) -> None:
        """Атомарно создаёт профиль и проверенную идентичность."""
        ...

    async def list_memberships(self, user_id: UUID) -> tuple[Membership, ...]:
        """Возвращает членства для вычисления области полномочий."""
        ...

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает историю назначений роли."""
        ...

    async def add_role_assignment(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Сохраняет назначение и новую версию полномочий атомарно."""
        ...

    async def revoke_role_assignment(
        self,
        updated_user: UserAccount,
        assignment_id: UUID,
        revoked_at: datetime,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Отзывает назначение и меняет версию полномочий атомарно."""
        ...

    async def bootstrap_admin(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        expected_authorization_version: int,
    ) -> None:
        """Однократно назначает первого администратора под DB singleton guard."""
        ...


class UserUnitOfWork(Protocol):
    """Открывает транзакцию и выполняет явный commit или rollback."""

    users: UserRepository

    async def __aenter__(self) -> "UserUnitOfWork":
        """Начинает транзакционный контекст."""
        ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Откатывает несохранённые изменения."""
        ...

    async def commit(self) -> None:
        """Фиксирует явно завершённую операцию."""
        ...


UnitOfWorkFactory = Callable[[], UserUnitOfWork]
