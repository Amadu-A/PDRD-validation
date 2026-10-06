# services/user-service/tests/unit/test_review_access.py

"""Назначение ревью: живая роль, CAS, идемпотентность и отсутствие лишних прав."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.review_access import ReviewAccessManagement
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.domain.access import (
    AccessSubject,
    AccessTier,
    Permission,
    Role,
    effective_permissions,
)
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.review_access import review_access_state
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
    access_subject_for,
)

AT = datetime(2026, 10, 6, 12, tzinfo=UTC)
ADMIN, TARGET = uuid4(), uuid4()
REVIEW_PERMISSIONS = {
    Permission.REVIEW_OWN_READ,
    Permission.REVIEW_GOLD_CREATE,
    Permission.REVIEW_FINDINGS_DECIDE,
    Permission.REVIEW_APPROVE,
    Permission.REVIEWED_PDF_DOWNLOAD,
    Permission.EXPERIENCE_CAPTURE,
}


def account(user_id):
    """Создаёт активного участника без ручного назначения ревью."""
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

    async def replace_review_access(
        self, updated_user, *, expected_authorization_version, actor_user_id, created_at
    ):
        """Сохраняет проверенную версию и актёра аудита."""
        assert (
            self.accounts[updated_user.user_id].authorization_version
            == expected_authorization_version
        )
        self.accounts[updated_user.user_id] = updated_user
        self.events.append(
            (actor_user_id, updated_user.review_access_enabled, created_at)
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
    return await ReviewAccessManagement(lambda: Work(users), clock=lambda: AT).change(
        actor_user_id=actor,
        target_user_id=target,
        enabled=enabled,
        authorization_version=version,
    )


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("enabled", [False, True])
def test_review_access_by_role_and_explicit_flag(role, enabled):
    """Полное ревью появляется по роли либо по отдельному назначению проектировщику."""
    subject = AccessSubject(
        AccessTier.MEMBER, frozenset({role}), review_access_enabled=enabled
    )
    permissions = effective_permissions(subject)
    state = review_access_state(subject)
    allowed = role is not Role.DESIGNER or enabled
    assert (
        permissions >= REVIEW_PERMISSIONS
        if allowed
        else not REVIEW_PERMISSIONS & permissions
    )
    assert state.review_access is allowed
    assert state.review_access_automatic is (role is not Role.DESIGNER)
    assert state.review_access_editable is (role is Role.DESIGNER)
    if role is Role.DESIGNER:
        assert (
            not {
                Permission.REVIEW_SCOPED_READ,
                Permission.ADMIN_ACCESS,
                Permission.NORMATIVE_WRITE,
                Permission.EXPERIENCE_VERSION_APPLY,
            }
            & permissions
        )


@pytest.mark.parametrize(
    "subject",
    [
        AccessSubject(AccessTier.GUEST, review_access_enabled=True),
        AccessSubject(AccessTier.REGISTERED_FREE, review_access_enabled=True),
        AccessSubject(AccessTier.MEMBER, review_access_enabled=True),
        AccessSubject(AccessTier.MEMBER, frozenset({Role.DESIGNER}), False, True),
    ],
)
def test_flag_without_active_working_role_does_not_grant_review(subject):
    """Флаг не даёт прав гостю, бесплатному, безролевому или заблокированному профилю."""
    assert not REVIEW_PERMISSIONS & effective_permissions(subject)
    assert not review_access_state(subject).review_access


@pytest.mark.asyncio
async def test_grant_repeat_and_revoke_change_version_only_once():
    """Выдача и отзыв повышают версию; повтор не создаёт аудит и новую версию."""
    users = Users()
    granted = await change(users, True)
    assert granted.user.authorization_version == 2
    assert granted.state.review_access
    repeated = await change(users, True, 2)
    assert repeated == granted
    revoked = await change(users, False, 2)
    assert revoked.user.authorization_version == 3
    assert not revoked.state.review_access
    assert users.events == [(ADMIN, True, AT), (ADMIN, False, AT)]
    assert users.commits == 2
    assert users.locks[:2] == sorted({ADMIN, TARGET}, key=str)


@pytest.mark.asyncio
async def test_stale_cas_and_nonadmin_cannot_change_review():
    """Старая версия и самостоятельная выдача проектировщиком отвергаются."""
    users = Users()
    with pytest.raises(AuthorizationConflict):
        await change(users, True, 9)
    with pytest.raises(AdminRequired):
        await change(users, True, actor=TARGET)
    users.roles[ADMIN] = (replace(users.roles[ADMIN][0], expires_at=AT),)
    with pytest.raises(AdminRequired):
        await change(users, True)
    assert users.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.DEPARTMENT_HEAD, Role.PLATFORM_ADMIN])
async def test_automatic_review_cannot_be_revoked_or_written_as_manual_flag(role):
    """Автоматический доступ не записывает ручной флаг и не позволяет снять галочку."""
    users = Users()
    users.roles[TARGET] = (assignment(TARGET, role),)
    result = await change(users, True)
    assert result.state.review_access_automatic
    assert not result.user.review_access_enabled
    with pytest.raises(ValueError, match="автоматически"):
        await change(users, False)
    users.roles[TARGET] = (assignment(TARGET, Role.DESIGNER),)
    subject = access_subject_for(users.accounts[TARGET], users.roles[TARGET], (), AT)
    assert not review_access_state(subject).review_access
    assert users.events == []


@pytest.mark.asyncio
async def test_invalid_target_is_not_modified():
    """Отсутствие, блокировка и снятая роль не дают создать ручное назначение."""
    users = Users()
    with pytest.raises(UserNotFound):
        await change(users, True, target=uuid4())
    users.accounts[TARGET] = replace(users.accounts[TARGET], status=UserStatus.BLOCKED)
    with pytest.raises(ValueError):
        await change(users, True)
    users.accounts[TARGET] = account(TARGET)
    users.roles[TARGET] = ()
    with pytest.raises(ValueError):
        await change(users, True)
    assert users.events == []
