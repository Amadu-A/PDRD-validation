# services/user-service/tests/unit/test_normative_access.py

"""Назначение удаление нормативных объектов: живая роль, CAS, идемпотентность и отсутствие лишних прав."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.normative_access import (
    NormativeAccessManagement,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.domain.access import (
    AccessSubject,
    AccessTier,
    Permission,
    Role,
    effective_permissions,
)
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.normative_access import normative_access_state
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)

AT = datetime(2026, 10, 6, 12, tzinfo=UTC)
ADMIN, TARGET = uuid4(), uuid4()


def account(user_id):
    """Создаёт активного участника без ручного назначения удаление нормативных объектов."""
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

    async def replace_normative_access(
        self, updated_user, *, expected_authorization_version, actor_user_id, created_at
    ):
        """Сохраняет проверенную версию и актёра аудита."""
        assert (
            self.accounts[updated_user.user_id].authorization_version
            == expected_authorization_version
        )
        self.accounts[updated_user.user_id] = updated_user
        self.events.append(
            (actor_user_id, updated_user.normative_access_enabled, created_at)
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
    return await NormativeAccessManagement(
        lambda: Work(users), clock=lambda: AT
    ).change(
        actor_user_id=actor,
        target_user_id=target,
        enabled=enabled,
        authorization_version=version,
    )


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("enabled", [False, True])
def test_normative_deletion_requires_admin_or_explicit_grant(role, enabled):
    """Флаг добавляет удаление; прежние создание и переименование руководителя сохраняются."""
    subject = AccessSubject(
        AccessTier.MEMBER, frozenset({role}), normative_access_enabled=enabled
    )
    permissions = effective_permissions(subject)
    state = normative_access_state(subject)
    allowed = role is Role.PLATFORM_ADMIN or enabled
    assert (Permission.NORMATIVE_DELETE in permissions) is allowed
    assert state.normative_access is allowed
    assert state.normative_access_automatic is (role is Role.PLATFORM_ADMIN)
    assert state.normative_access_editable is (role is not Role.PLATFORM_ADMIN)
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
    assert granted.state.normative_access
    assert await change(users, True, 2) == granted
    revoked = await change(users, False, 2)
    assert revoked.user.authorization_version == 3
    assert not revoked.state.normative_access
    with pytest.raises(AuthorizationConflict):
        await change(users, True, 2)
    with pytest.raises(AdminRequired):
        await change(users, True, 3, actor=TARGET)
    assert users.roles[TARGET][0].role is role
    assert users.events == [(ADMIN, True, AT), (ADMIN, False, AT)]
    assert users.commits == 2


@pytest.mark.asyncio
async def test_admin_automatic_right_cannot_be_revoked():
    """Административное удаление автоматическое и не записывает ручной флаг."""
    users = Users()
    users.roles[TARGET] = (assignment(TARGET, Role.PLATFORM_ADMIN),)
    result = await change(users, True)
    assert result.state.normative_access_automatic
    assert not result.user.normative_access_enabled
    with pytest.raises(ValueError, match="автоматически"):
        await change(users, False)
    assert users.events == []


@pytest.mark.asyncio
async def test_blocked_expired_or_missing_role_cannot_get_delete_permission():
    """Неактивный профиль или истёкшая роль не допускают выдачу права."""
    users = Users()
    users.accounts[TARGET] = replace(users.accounts[TARGET], status=UserStatus.BLOCKED)
    with pytest.raises(ValueError):
        await change(users, True)
    users.accounts[TARGET] = account(TARGET)
    users.roles[TARGET] = (replace(users.roles[TARGET][0], expires_at=AT),)
    with pytest.raises(ValueError):
        await change(users, True)
    with pytest.raises(UserNotFound):
        await change(users, True, target=uuid4())
    assert users.events == []
