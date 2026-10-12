# services/user-service/tests/unit/test_equipment_sources_access.py

"""Назначение управление источниками оборудования: живая роль, CAS, идемпотентность и отсутствие лишних прав."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.equipment_sources_access import (
    EquipmentSourcesAccessManagement,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.domain.access import (
    AccessSubject,
    AccessTier,
    Permission,
    Role,
    effective_permissions,
)
from pdrd_user_service.domain.equipment_sources_access import (
    equipment_sources_access_state,
)
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)

AT = datetime(2026, 10, 6, 12, tzinfo=UTC)
ADMIN, TARGET = uuid4(), uuid4()


def account(user_id):
    """Создаёт активного участника без ручного назначения управление источниками оборудования."""
    return UserAccount(
        user_id,
        UserKind.CORPORATE,
        AccessTier.MEMBER,
        UserStatus.ACTIVE,
        "Сотрудник",
        AT - timedelta(days=1),
        login=str(user_id),
    )


def assignment(user_id, role):
    """Создаёт живое назначение с допустимой областью роли."""
    scope = {
        Role.DESIGNER: ScopeKind.OWN,
        Role.DEPARTMENT_HEAD: ScopeKind.SECTIONS,
        Role.PLATFORM_ADMIN: ScopeKind.PLATFORM,
    }[role]
    return RoleAssignment(
        uuid4(),
        user_id,
        role,
        RoleSource.LOCAL,
        RoleScope(scope),
        AT - timedelta(hours=1),
    )


class Users:
    """Имитирует транзакционные порты и записывает назначение с аудитом."""

    def __init__(self):
        """Готовит администратора и проектировщика."""
        self.accounts = {ADMIN: account(ADMIN), TARGET: account(TARGET)}
        self.roles = {
            ADMIN: (assignment(ADMIN, Role.PLATFORM_ADMIN),),
            TARGET: (assignment(TARGET, Role.DESIGNER),),
        }
        self.events = []
        self.locks = []
        self.commits = 0

    async def get_user(self, user_id, *, for_update=False):
        """Фиксирует порядок блокировок профилей."""
        assert for_update
        self.locks.append(user_id)
        return self.accounts.get(user_id)

    async def list_assignments(self, user_id, *, for_update=False):
        """Возвращает текущие назначения."""
        assert for_update
        return self.roles.get(user_id, ())

    async def list_memberships(self, user_id):
        """У собственных заданий нет области отдела."""
        return ()

    async def replace_equipment_sources_access(
        self, updated_user, *, expected_authorization_version, actor_user_id, created_at
    ):
        """Сохраняет проверенную версию и актёра аудита."""
        assert (
            self.accounts[updated_user.user_id].authorization_version
            == expected_authorization_version
        )
        self.accounts[updated_user.user_id] = updated_user
        self.events.append(
            (actor_user_id, updated_user.equipment_sources_access_enabled, created_at)
        )


class Work:
    """Считает явные фиксации сценария."""

    def __init__(self, users):
        """Получает общую память."""
        self.users = users

    async def __aenter__(self):
        """Открывает рабочую область."""
        return self

    async def __aexit__(self, *args):
        """Закрывает рабочую область."""

    async def commit(self):
        """Фиксирует результат."""
        self.users.commits += 1


async def change(users, enabled, version=1, *, actor=ADMIN, target=TARGET):
    """Запускает реальный сценарий через тестовые порты."""
    return await EquipmentSourcesAccessManagement(
        lambda: Work(users), clock=lambda: AT
    ).change(
        actor_user_id=actor,
        target_user_id=target,
        enabled=enabled,
        authorization_version=version,
    )


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("enabled", [False, True])
def test_equipment_sources_requires_admin_or_explicit_grant(role, enabled):
    """Флаг даёт только управление источниками оборудования, сохраняя права роли."""
    subject = AccessSubject(
        AccessTier.MEMBER, frozenset({role}), equipment_sources_access_enabled=enabled
    )
    permissions = effective_permissions(subject)
    state = equipment_sources_access_state(subject)
    allowed = role is Role.PLATFORM_ADMIN or enabled
    assert (Permission.EQUIPMENT_SOURCES_MANAGE in permissions) is allowed
    assert state.equipment_sources_access is allowed
    assert state.equipment_sources_access_automatic is (role is Role.PLATFORM_ADMIN)
    assert state.equipment_sources_access_editable is (role is not Role.PLATFORM_ADMIN)
    assert (Permission.NORMATIVE_WRITE in permissions) is (role is not Role.DESIGNER)
    if role is not Role.PLATFORM_ADMIN:
        assert Permission.ADMIN_ACCESS not in permissions


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.DESIGNER, Role.DEPARTMENT_HEAD])
async def test_grant_revoke_repeat_and_cas_preserve_roles(role):
    """Назначение не меняет роль и не возвращает ручной отзыв при повторе."""
    users = Users()
    users.roles[TARGET] = (assignment(TARGET, role),)
    granted = await change(users, True)
    assert granted.user.authorization_version == 2
    assert granted.state.equipment_sources_access
    assert await change(users, True, 2) == granted
    revoked = await change(users, False, 2)
    assert revoked.user.authorization_version == 3
    assert not revoked.state.equipment_sources_access
    with pytest.raises(AuthorizationConflict):
        await change(users, True, 2)
    with pytest.raises(AdminRequired):
        await change(users, True, 3, actor=TARGET)
    assert users.roles[TARGET][0].role is role
    assert users.events == [(ADMIN, True, AT), (ADMIN, False, AT)]
    assert users.commits == 2


@pytest.mark.asyncio
async def test_admin_automatic_right_cannot_be_revoked():
    """Управление источниками оборудования администратором автоматическое и не записывает ручной флаг."""
    users = Users()
    users.roles[TARGET] = (assignment(TARGET, Role.PLATFORM_ADMIN),)
    result = await change(users, True)
    assert result.state.equipment_sources_access_automatic
    assert not result.user.equipment_sources_access_enabled
    with pytest.raises(ValueError, match="автоматически"):
        await change(users, False)
    assert users.events == []


@pytest.mark.asyncio
async def test_inactive_accounts_and_missing_users_cannot_get_access():
    """Заблокированный или неподтверждённый профиль не получает доступ."""
    for status in (UserStatus.BLOCKED, UserStatus.PENDING_VERIFICATION):
        users = Users()
        users.accounts[TARGET] = replace(
            users.accounts[TARGET],
            kind=UserKind.EXTERNAL,
            status=status,
            email="test@example.test",
        )
        with pytest.raises(ValueError):
            await change(users, True)
        assert users.events == []
    with pytest.raises(UserNotFound):
        await change(Users(), True, target=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [AccessTier.REGISTERED_FREE, AccessTier.MEMBER])
async def test_explicit_access_without_worker_role(tier):
    """Админ может назначить каталог активному пользователю без рабочей роли."""
    users = Users()
    users.roles[TARGET] = ()
    users.accounts[TARGET] = replace(users.accounts[TARGET], tier=tier)
    result = await change(users, True)
    assert result.state.equipment_sources_access
    assert result.state.equipment_sources_access_editable
    assert not result.state.equipment_sources_access_automatic
    await change(users, False, 2)
    assert users.events == [(ADMIN, True, AT), (ADMIN, False, AT)]


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("tier", list(AccessTier))
def test_manual_access_does_not_bypass_account_activity(tier, active):
    """Гость и неактивный профиль не получают право даже с сохранённым флагом."""
    subject = AccessSubject(tier, active=active, equipment_sources_access_enabled=True)
    permissions = effective_permissions(subject)
    assert (Permission.EQUIPMENT_SOURCES_MANAGE in permissions) is (
        active and tier is not AccessTier.GUEST
    )
    assert Permission.ADMIN_ACCESS not in permissions
    assert Permission.USERS_ROLES_ASSIGN not in permissions
    assert Permission.NORMATIVE_DELETE not in permissions
