# services/user-service/src/pdrd_user_service/application/ports/repository.py

"""Асинхронные порты транзакционного хранилища пользователей."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pdrd_user_service.domain.identity import (
    Department,
    ExternalIdentity,
    Membership,
    Organization,
    UserAccount,
)
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

    async def find_by_login(self, login: str) -> UserAccount | None:
        """Ищет источник входа; stable key остаётся ключом привязки профиля."""
        ...

    async def list_users(
        self, *, limit: int, offset: int
    ) -> tuple[tuple[UserAccount, ...], int]:
        """Возвращает страницу профилей и их общее число."""
        ...

    async def create_user(self, user: UserAccount, identity: ExternalIdentity) -> None:
        """Атомарно создаёт профиль и устойчивую идентичность."""
        ...

    async def initialize_access(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        section_ids: tuple[UUID, ...],
        *,
        expected_authorization_version: int,
        activate_external: bool = False,
    ) -> None:
        """Однократно сохраняет подтверждение, роль, разделы и аудит одной версией."""
        ...

    async def activate_external(
        self, updated_user: UserAccount, expected_authorization_version: int
    ) -> None:
        """Активирует ожидающий внешний профиль и повышает версию прав."""
        ...

    async def list_memberships(self, user_id: UUID) -> tuple[Membership, ...]:
        """Возвращает членства для вычисления области полномочий."""
        ...

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Читает организацию для проверки административной операции."""
        ...

    async def list_organizations(
        self, *, limit: int, offset: int
    ) -> tuple[tuple[Organization, ...], int]:
        """Возвращает ограниченную страницу организаций."""
        ...

    async def create_organization(self, organization: Organization) -> None:
        """Создаёт организацию в текущей транзакции."""
        ...

    async def get_department(
        self, organization_id: UUID, department_id: UUID
    ) -> Department | None:
        """Читает отдел только внутри указанной организации."""
        ...

    async def list_departments(
        self, organization_id: UUID, *, limit: int, offset: int
    ) -> tuple[tuple[Department, ...], int]:
        """Возвращает ограниченную страницу отделов организации."""
        ...

    async def create_department(self, department: Department) -> None:
        """Создаёт отдел с проверенным родителем."""
        ...

    async def get_department_membership(
        self, user_id: UUID, organization_id: UUID, department_id: UUID
    ) -> Membership | None:
        """Читает точное членство для изменения статуса."""
        ...

    async def set_department_membership(
        self,
        updated_user: UserAccount,
        membership: Membership,
        *,
        expected_authorization_version: int,
    ) -> None:
        """Атомарно меняет членство и версию полномочий."""
        ...

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает историю назначений роли."""
        ...

    async def replace_review_access(
        self,
        updated_user: UserAccount,
        *,
        expected_authorization_version: int,
        actor_user_id: UUID,
        created_at: datetime,
    ) -> None:
        """Атомарно сохраняет назначение ревью, новую версию прав и аудит."""
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

    async def replace_worker_roles(
        self,
        updated_user: UserAccount,
        previous_assignment_ids: tuple[UUID, ...],
        new_assignment: RoleAssignment | None,
        revoked_at: datetime,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Одной транзакцией меняет рабочие роли, версию прав и аудит."""
        ...

    async def lock_section_catalog(self) -> None:
        """Сериализует первичное назначение и выдачу новых разделов."""
        ...

    async def list_distributed_sections(self) -> tuple[UUID, ...]:
        """Читает UUID новых разделов, распределённых после снимка Knowledge."""
        ...

    async def lock_catalog_users(self, actor_user_id: UUID) -> tuple[UserAccount, ...]:
        """Блокирует активных участников и актёра в едином порядке UUID."""
        ...

    async def reserve_section_distribution(
        self, section_id: UUID, actor_user_id: UUID, created_at: datetime
    ) -> bool:
        """Резервирует одноразовую выдачу; повтор не восстанавливает ручные отзывы."""
        ...

    async def list_sections(self, user_id: UUID) -> tuple[UUID, ...]:
        """Читает UUID назначенных разделов."""
        ...

    async def replace_sections(
        self,
        user_id: UUID,
        section_ids: tuple[UUID, ...],
        *,
        actor_user_id: UUID,
        authorization_version: int,
        created_at: datetime,
    ) -> None:
        """Сохраняет разделы и аудит вместе с изменением версии прав."""
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
