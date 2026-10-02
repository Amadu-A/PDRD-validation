# services/user-service/tests/unit/test_user_directory.py

"""Проверяет сценарии каталога без настоящего PostgreSQL и HTTP."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    BootstrapAlreadyPerformed,
    IdentityConflict,
)
from pdrd_user_service.application.use_cases.bootstrap import bootstrap_first_admin
from pdrd_user_service.application.use_cases.users import AdminRequired, UserDirectory
from pdrd_user_service.domain.access import AccessTier, Permission, Role
from pdrd_user_service.domain.identity import (
    ExternalIdentity,
    Membership,
    UserAccount,
    UserKind,
    UserStatus,
)
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)

AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
ADMIN = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
TARGET = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ORG = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
OTHER_ORG = UUID("dddddddd-dddd-4ddd-dddd-dddddddddddd")
DEPT = UUID("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee")
ROLE_ID = UUID("ffffffff-ffff-4fff-ffff-ffffffffffff")
NEW_ROLE_ID = UUID("99999999-9999-4999-8999-999999999999")


def member(user_id: UUID, *, status: UserStatus = UserStatus.ACTIVE) -> UserAccount:
    """Создаёт сотрудника с ожидаемым состоянием."""
    return UserAccount(
        user_id=user_id,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=status,
        display_name="Сотрудник",
        created_at=AT - timedelta(days=1),
        login=str(user_id),
    )


def admin_assignment() -> RoleAssignment:
    """Создаёт действующую администраторскую роль из хранилища."""
    return RoleAssignment(
        assignment_id=ROLE_ID,
        user_id=ADMIN,
        role=Role.PLATFORM_ADMIN,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.PLATFORM),
        created_at=AT - timedelta(hours=1),
    )


class MemoryRepository:
    """Хранит профили и назначения для проверки прикладной политики."""

    def __init__(self) -> None:
        """Создаёт изолированное пустое состояние."""
        self.users: dict[UUID, UserAccount] = {}
        self.identities: dict[tuple[str, str, str], UUID] = {}
        self.memberships: dict[UUID, tuple[Membership, ...]] = {}
        self.assignments: dict[UUID, tuple[RoleAssignment, ...]] = {}
        self.bootstrapped = False
        self.audit_actors: list[UUID] = []

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Возвращает текущий профиль."""
        del for_update
        return self.users.get(user_id)

    async def find_identity(
        self, provider_id: str, namespace: str, subject: str
    ) -> UserAccount | None:
        """Ищет только по полному stable key."""
        user_id = self.identities.get((provider_id, namespace, subject))
        return self.users.get(user_id) if user_id is not None else None

    async def create_user(self, user: UserAccount, identity: ExternalIdentity) -> None:
        """Создаёт идентичность и профиль вместе."""
        if identity.stable_key in self.identities:
            raise IdentityConflict
        self.users[user.user_id] = user
        self.identities[identity.stable_key] = user.user_id

    async def list_memberships(self, user_id: UUID) -> tuple[Membership, ...]:
        """Возвращает снимок членства."""
        return self.memberships.get(user_id, ())

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает снимок назначений."""
        del for_update
        return self.assignments.get(user_id, ())

    async def add_role_assignment(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Проверяет версию и сохраняет назначение."""
        current = self.users[updated_user.user_id]
        if current.authorization_version != expected_authorization_version:
            raise AuthorizationConflict
        self.users[updated_user.user_id] = updated_user
        self.audit_actors.append(actor_user_id)
        self.assignments[updated_user.user_id] = (
            *self.assignments.get(updated_user.user_id, ()),
            assignment,
        )

    async def revoke_role_assignment(
        self,
        updated_user: UserAccount,
        assignment_id: UUID,
        revoked_at: datetime,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Проверяет версию и отзывает назначение."""
        current = self.users[updated_user.user_id]
        if current.authorization_version != expected_authorization_version:
            raise AuthorizationConflict
        self.users[updated_user.user_id] = updated_user
        self.audit_actors.append(actor_user_id)
        self.assignments[updated_user.user_id] = tuple(
            replace(item, revoked_at=revoked_at)
            if item.assignment_id == assignment_id
            else item
            for item in self.assignments[updated_user.user_id]
        )

    async def bootstrap_admin(
        self,
        updated_user: UserAccount,
        assignment: RoleAssignment,
        expected_authorization_version: int,
    ) -> None:
        """Имитирует одноразовый охранный маркер хранилища."""
        if self.bootstrapped:
            raise BootstrapAlreadyPerformed
        current = self.users[updated_user.user_id]
        if current.authorization_version != expected_authorization_version:
            raise AuthorizationConflict
        self.users[updated_user.user_id] = updated_user
        self.assignments[updated_user.user_id] = (
            *self.assignments.get(updated_user.user_id, ()),
            assignment,
        )
        self.bootstrapped = True


class MemoryUnitOfWork:
    """Предоставляет тесту тот же явный транзакционный интерфейс."""

    def __init__(self, users: MemoryRepository) -> None:
        """Сохраняет ссылку на репозиторий."""
        self.users = users
        self.commits = 0

    async def __aenter__(self) -> "MemoryUnitOfWork":
        """Открывает контекст."""
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Завершает тестовый контекст."""
        del exc_type, exc, tb

    async def commit(self) -> None:
        """Отмечает фиксацию сценария."""
        self.commits += 1


@pytest.mark.asyncio
async def test_provision_is_idempotent_only_by_stable_identity() -> None:
    """Email не связывает два независимых внешних профиля."""
    store = MemoryRepository()
    ids = iter((TARGET, ADMIN))
    directory = UserDirectory(
        lambda: MemoryUnitOfWork(store), clock=lambda: AT, new_id=lambda: next(ids)
    )
    first = await directory.provision(
        provider_id="oidc",
        namespace="pdrd",
        subject="account-1",
        kind=UserKind.EXTERNAL,
        display_name="Клиент",
        email="one@example.org",
    )
    repeated = await directory.provision(
        provider_id="oidc",
        namespace="pdrd",
        subject="account-1",
        kind=UserKind.EXTERNAL,
        display_name="Переименование",
        email="other@example.org",
    )
    second = await directory.provision(
        provider_id="oidc",
        namespace="pdrd",
        subject="account-2",
        kind=UserKind.EXTERNAL,
        display_name="Другой",
        email="one@example.org",
    )
    assert repeated == first
    assert second.user_id != first.user_id
    assert first.tier is AccessTier.REGISTERED_FREE
    assert len(store.users) == 2

    with pytest.raises(IdentityConflict, match="Тип учётной записи"):
        await directory.provision(
            provider_id="oidc",
            namespace="pdrd",
            subject="account-1",
            kind=UserKind.CORPORATE,
            display_name="Неверный тип",
        )


@pytest.mark.asyncio
async def test_role_change_requires_database_admin_and_scoped_membership() -> None:
    """Назначение не доверяет заголовку роли и чужой организации."""
    store = MemoryRepository()
    store.users[ADMIN] = member(ADMIN)
    store.users[TARGET] = member(TARGET)
    store.memberships[TARGET] = (Membership(TARGET, ORG, DEPT),)
    directory = UserDirectory(
        lambda: MemoryUnitOfWork(store), clock=lambda: AT, new_id=lambda: NEW_ROLE_ID
    )
    scope = RoleScope(ScopeKind.DEPARTMENT, ORG, DEPT)

    with pytest.raises(AdminRequired):
        await directory.assign(
            actor_user_id=ADMIN,
            target_user_id=TARGET,
            role=Role.DEPARTMENT_HEAD,
            scope=scope,
        )

    store.assignments[ADMIN] = (admin_assignment(),)
    with pytest.raises(ValueError, match="область"):
        await directory.assign(
            actor_user_id=ADMIN,
            target_user_id=TARGET,
            role=Role.DEPARTMENT_HEAD,
            scope=RoleScope(ScopeKind.DEPARTMENT, OTHER_ORG, DEPT),
        )

    change = await directory.assign(
        actor_user_id=ADMIN,
        target_user_id=TARGET,
        role=Role.DEPARTMENT_HEAD,
        scope=scope,
    )
    assert change.user.authorization_version == 2
    assert store.audit_actors == [ADMIN]
    snapshot = await directory.permissions(TARGET)
    assert Role.DEPARTMENT_HEAD in snapshot.roles
    assert Permission.REVIEW_APPROVE in snapshot.permissions
    assert snapshot.authorization_version == 2

    revoked = await directory.revoke(
        actor_user_id=ADMIN,
        target_user_id=TARGET,
        assignment_id=NEW_ROLE_ID,
    )
    assert revoked.user.authorization_version == 3
    assert store.audit_actors == [ADMIN, ADMIN]
    assert revoked.assignment.revoked_at == AT
    assert Role.DEPARTMENT_HEAD not in (await directory.permissions(TARGET)).roles


@pytest.mark.asyncio
async def test_blocked_admin_and_platform_admin_generic_assignment_denied() -> None:
    """Недействующий админ и обход bootstrap не меняют назначений."""
    store = MemoryRepository()
    store.users[ADMIN] = member(ADMIN, status=UserStatus.BLOCKED)
    store.users[TARGET] = member(TARGET)
    store.assignments[ADMIN] = (admin_assignment(),)
    directory = UserDirectory(lambda: MemoryUnitOfWork(store), clock=lambda: AT)
    with pytest.raises(AdminRequired):
        await directory.assign(
            actor_user_id=ADMIN,
            target_user_id=TARGET,
            role=Role.DESIGNER,
            scope=RoleScope(ScopeKind.OWN),
        )
    store.users[ADMIN] = member(ADMIN)
    with pytest.raises(ValueError, match="Администратора"):
        await directory.assign(
            actor_user_id=ADMIN,
            target_user_id=TARGET,
            role=Role.PLATFORM_ADMIN,
            scope=RoleScope(ScopeKind.PLATFORM),
        )


@pytest.mark.asyncio
async def test_admin_expiring_while_target_is_loaded_cannot_assign() -> None:
    """Повторная проверка срока роли защищает запись после ожидания цели."""
    store = MemoryRepository()
    store.users[ADMIN] = member(ADMIN)
    store.users[TARGET] = member(TARGET)
    store.assignments[ADMIN] = (
        replace(admin_assignment(), expires_at=AT + timedelta(seconds=1)),
    )
    times = iter((AT, AT + timedelta(seconds=2)))
    directory = UserDirectory(
        lambda: MemoryUnitOfWork(store), clock=lambda: next(times)
    )
    with pytest.raises(AdminRequired):
        await directory.assign(
            actor_user_id=ADMIN,
            target_user_id=TARGET,
            role=Role.DESIGNER,
            scope=RoleScope(ScopeKind.OWN),
        )
    assert store.users[TARGET].authorization_version == 1
    assert store.audit_actors == []


@pytest.mark.asyncio
async def test_bootstrap_admin_once_only_for_existing_active_employee() -> None:
    """Первого администратора создаёт отдельная атомарная операция."""
    store = MemoryRepository()
    store.users[TARGET] = member(TARGET)
    assignment = await bootstrap_first_admin(
        lambda: MemoryUnitOfWork(store),
        TARGET,
        clock=lambda: AT,
        new_id=lambda: ROLE_ID,
    )
    assert assignment.role is Role.PLATFORM_ADMIN
    assert store.users[TARGET].authorization_version == 2
    with pytest.raises(BootstrapAlreadyPerformed):
        await bootstrap_first_admin(
            lambda: MemoryUnitOfWork(store),
            TARGET,
            clock=lambda: AT,
            new_id=lambda: ROLE_ID,
        )
