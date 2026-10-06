# services/user-service/tests/unit/test_replace_role.py

"""Проверяет замену роли, CAS и границы bootstrap без PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.replace_role import ReplaceWorkerRole
from pdrd_user_service.application.use_cases.users import AdminRequired
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import (
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

AT = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
ADMIN_ID = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
TARGET_ID = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ORG_ID = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
DEPT_ID = UUID("dddddddd-dddd-4ddd-dddd-dddddddddddd")
ADMIN_ROLE_ID = UUID("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee")
DESIGNER_ROLE_ID = UUID("ffffffff-ffff-4fff-ffff-ffffffffffff")
NEW_ROLE_ID = UUID("11111111-1111-4111-8111-111111111111")


def member(user_id: UUID) -> UserAccount:
    """Создаёт активного сотрудника с рабочим уровнем доступа."""
    return UserAccount(
        user_id=user_id,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Сотрудник",
        login=str(user_id),
        created_at=AT - timedelta(days=1),
    )


def role_assignment(
    assignment_id: UUID, user_id: UUID, role: Role, scope: RoleScope
) -> RoleAssignment:
    """Создаёт действующее локальное назначение."""
    return RoleAssignment(
        assignment_id=assignment_id,
        user_id=user_id,
        role=role,
        source=RoleSource.LOCAL,
        scope=scope,
        created_at=AT - timedelta(hours=1),
    )


class Users:
    """Подменяет транзакционное хранилище с контролем версии."""

    def __init__(self) -> None:
        """Создаёт администратора и проектировщика с членством в отделе."""
        self.accounts = {ADMIN_ID: member(ADMIN_ID), TARGET_ID: member(TARGET_ID)}
        self.assignments = {
            ADMIN_ID: (
                role_assignment(
                    ADMIN_ROLE_ID,
                    ADMIN_ID,
                    Role.PLATFORM_ADMIN,
                    RoleScope(ScopeKind.PLATFORM),
                ),
            ),
            TARGET_ID: (
                role_assignment(
                    DESIGNER_ROLE_ID,
                    TARGET_ID,
                    Role.DESIGNER,
                    RoleScope(ScopeKind.OWN),
                ),
            ),
        }
        self.memberships = (Membership(TARGET_ID, ORG_ID, DEPT_ID),)
        self.writes = 0
        self.audit: list[tuple[UUID, tuple[UUID, ...], UUID | None]] = []

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Возвращает профиль под тестовой блокировкой."""
        assert user_id != ADMIN_ID or for_update
        return self.accounts.get(user_id)

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает назначенные пользователю роли."""
        assert user_id != ADMIN_ID or for_update
        return self.assignments.get(user_id, ())

    async def list_memberships(self, user_id: UUID) -> tuple[Membership, ...]:
        """Выдаёт одно членство целевого сотрудника."""
        return self.memberships if user_id == TARGET_ID else ()

    async def replace_worker_roles(
        self,
        updated_user: UserAccount,
        previous_assignment_ids: tuple[UUID, ...],
        new_assignment: RoleAssignment | None,
        revoked_at: datetime,
        expected_authorization_version: int,
        actor_user_id: UUID,
    ) -> None:
        """Имитирует атомарный отзыв и назначение с одной новой версией."""
        current = self.accounts[updated_user.user_id]
        if current.authorization_version != expected_authorization_version:
            raise AuthorizationConflict("Устаревшая версия")
        self.accounts[updated_user.user_id] = updated_user
        previous = tuple(
            replace(item, revoked_at=revoked_at)
            if item.assignment_id in previous_assignment_ids
            else item
            for item in self.assignments[updated_user.user_id]
        )
        self.assignments[updated_user.user_id] = (
            (*previous, new_assignment) if new_assignment else previous
        )
        self.writes += 1
        self.audit.append(
            (
                actor_user_id,
                previous_assignment_ids,
                new_assignment.assignment_id if new_assignment else None,
            )
        )

    async def list_sections(self, user_id: UUID) -> tuple[UUID, ...]:
        """Возвращает назначенные разделы без обращения к чужому каталогу."""
        return getattr(self, "sections", {}).get(user_id, ())

    async def replace_sections(
        self, user_id: UUID, section_ids: tuple[UUID, ...], **audit: object
    ) -> None:
        """Фиксирует назначения и аудит в общей транзакции замены роли."""
        self.sections = {user_id: section_ids}
        self.section_audit = audit


class Work:
    """Предоставляет общей памяти интерфейс Unit of Work."""

    def __init__(self, users: Users) -> None:
        """Принимает тестовый каталог."""
        self.users = users

    async def __aenter__(self) -> "Work":
        """Открывает рабочую область."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Завершает рабочую область."""
        del args

    async def commit(self) -> None:
        """Подтверждает запись тестового репозитория."""


def service(users: Users) -> ReplaceWorkerRole:
    """Создаёт сценарий с фиксированным временем и UUID нового назначения."""
    return ReplaceWorkerRole(
        lambda: Work(users), clock=lambda: AT, new_id=lambda: NEW_ROLE_ID
    )


@pytest.mark.asyncio
async def test_role_replacement_revokes_old_and_adds_new_with_one_version() -> None:
    """Одна команда меняет роль и пишет один новый номер версии."""
    users = Users()
    result = await service(users).replace(
        actor_user_id=ADMIN_ID,
        target_user_id=TARGET_ID,
        role=Role.DEPARTMENT_HEAD,
        scope=RoleScope(ScopeKind.DEPARTMENT, ORG_ID, DEPT_ID),
        authorization_version=1,
    )
    assert result.user.authorization_version == 2
    assert result.roles == (Role.DEPARTMENT_HEAD,)
    assert len(result.assignments) == 1
    assert result.assignments[0].assignment_id == NEW_ROLE_ID
    assert users.assignments[TARGET_ID][0].revoked_at == AT
    assert users.audit == [(ADMIN_ID, (DESIGNER_ROLE_ID,), NEW_ROLE_ID)]


@pytest.mark.asyncio
async def test_null_role_revokes_and_matching_role_is_noop() -> None:
    """Снятие роли повышает версию, а сохранение без изменений не пишет БД."""
    users = Users()
    application = service(users)
    unchanged = await application.replace(
        actor_user_id=ADMIN_ID,
        target_user_id=TARGET_ID,
        role=Role.DESIGNER,
        scope=RoleScope(ScopeKind.OWN),
        authorization_version=1,
    )
    assert unchanged.user.authorization_version == 1
    assert users.writes == 0
    revoked = await application.replace(
        actor_user_id=ADMIN_ID,
        target_user_id=TARGET_ID,
        role=None,
        scope=None,
        authorization_version=1,
    )
    assert revoked.roles == ()
    assert revoked.assignments == ()
    assert revoked.user.authorization_version == 2
    assert users.writes == 1


@pytest.mark.asyncio
async def test_cas_and_admin_role_are_enforced_before_mutation() -> None:
    """Устаревшая версия, отзыв админа и чужой актёр закрывают изменение."""
    users = Users()
    application = service(users)
    with pytest.raises(AuthorizationConflict):
        await application.replace(
            actor_user_id=ADMIN_ID,
            target_user_id=TARGET_ID,
            role=None,
            scope=None,
            authorization_version=2,
        )
    with pytest.raises(AdminRequired):
        await application.replace(
            actor_user_id=TARGET_ID,
            target_user_id=TARGET_ID,
            role=None,
            scope=None,
            authorization_version=1,
        )
    assert users.writes == 0


@pytest.mark.asyncio
async def test_role_detail_requires_admin_and_exposes_current_version() -> None:
    """Выбранный пользователь читается только при действующей роли админа."""
    users = Users()
    detail = await service(users).inspect(
        actor_user_id=ADMIN_ID, target_user_id=TARGET_ID
    )
    assert detail.user.authorization_version == 1
    assert detail.roles == (Role.DESIGNER,)
    users.accounts[ADMIN_ID] = replace(
        users.accounts[ADMIN_ID], status=UserStatus.BLOCKED
    )
    with pytest.raises(AdminRequired):
        await service(users).inspect(actor_user_id=ADMIN_ID, target_user_id=TARGET_ID)


@pytest.mark.asyncio
async def test_admin_can_promote_other_user_but_cannot_demote_self() -> None:
    """Выдача админских прав повышает версию; собственные права защищены."""
    users = Users()
    result = await service(users).replace(
        actor_user_id=ADMIN_ID,
        target_user_id=TARGET_ID,
        role=Role.PLATFORM_ADMIN,
        scope=RoleScope(ScopeKind.PLATFORM),
        authorization_version=1,
    )
    assert result.roles == (Role.PLATFORM_ADMIN,)
    assert result.user.authorization_version == 2
    with pytest.raises(ValueError, match="собственные"):
        await service(users).replace(
            actor_user_id=ADMIN_ID,
            target_user_id=ADMIN_ID,
            role=None,
            scope=None,
            authorization_version=1,
        )


@pytest.mark.asyncio
async def test_admin_can_promote_verified_external_user() -> None:
    """Подтверждённый внешний аккаунт получает member и роль одной операцией."""
    users = Users()
    users.accounts[TARGET_ID] = replace(
        users.accounts[TARGET_ID],
        kind=UserKind.EXTERNAL,
        tier=AccessTier.REGISTERED_FREE,
        email="client@example.test",
    )
    result = await service(users).replace(
        actor_user_id=ADMIN_ID,
        target_user_id=TARGET_ID,
        role=Role.PLATFORM_ADMIN,
        scope=RoleScope(ScopeKind.PLATFORM),
        authorization_version=1,
    )
    assert result.user.tier is AccessTier.MEMBER
    assert result.roles == (Role.PLATFORM_ADMIN,)


@pytest.mark.asyncio
async def test_sections_change_with_same_role_and_one_cas_version() -> None:
    """Изменение только разделов не теряется в прежней ветке сохранения одинаковой роли."""
    users = Users()
    result = await service(users).replace(
        actor_user_id=ADMIN_ID,
        target_user_id=TARGET_ID,
        role=Role.DESIGNER,
        scope=RoleScope(ScopeKind.OWN),
        authorization_version=1,
        section_ids=(ORG_ID, DEPT_ID, ORG_ID),
    )
    assert result.user.authorization_version == 2
    assert users.sections[TARGET_ID] == (ORG_ID, DEPT_ID)
    assert users.section_audit["actor_user_id"] == ADMIN_ID
    assert users.section_audit["authorization_version"] == 2
    with pytest.raises(AuthorizationConflict):
        await service(users).replace(
            actor_user_id=ADMIN_ID,
            target_user_id=TARGET_ID,
            role=Role.DESIGNER,
            scope=RoleScope(ScopeKind.OWN),
            authorization_version=1,
            section_ids=(),
        )
    assert users.sections[TARGET_ID] == (ORG_ID, DEPT_ID)


@pytest.mark.asyncio
async def test_head_can_use_multiple_sections_without_department_membership() -> None:
    """Новая область руководителя не требует отдельного справочника организаций и отделов."""
    users = Users()
    users.memberships = ()
    result = await service(users).replace(
        actor_user_id=ADMIN_ID,
        target_user_id=TARGET_ID,
        role=Role.DEPARTMENT_HEAD,
        scope=RoleScope(ScopeKind.SECTIONS),
        authorization_version=1,
        section_ids=(ORG_ID, DEPT_ID),
    )
    assert result.roles == (Role.DEPARTMENT_HEAD,)
    assert result.assignments[0].scope.kind is ScopeKind.SECTIONS


@pytest.mark.asyncio
async def test_section_snapshot_allows_self_and_admin_only() -> None:
    """Назначения другого профиля не выдаются по произвольному служебному Actor ID."""
    from pdrd_user_service.application.use_cases.section_access import UserSections

    users = Users()
    users.sections = {TARGET_ID: (ORG_ID, DEPT_ID)}
    access = UserSections(lambda: Work(users))
    own = await access.read(actor_user_id=TARGET_ID, target_user_id=TARGET_ID)
    assert own.section_ids == (ORG_ID, DEPT_ID)
    assert not own.all_sections
    admin = await access.read(actor_user_id=ADMIN_ID, target_user_id=TARGET_ID)
    assert admin.section_ids == own.section_ids
    with pytest.raises(AdminRequired):
        await access.read(actor_user_id=TARGET_ID, target_user_id=ADMIN_ID)


async def test_inactive_user_cannot_receive_sections_without_a_role() -> None:
    """Изменение только разделов не обходит проверку заблокированного профиля."""
    users = Users()
    users.accounts[TARGET_ID] = replace(
        users.accounts[TARGET_ID], status=UserStatus.BLOCKED
    )
    with pytest.raises(ValueError, match="активному"):
        await ReplaceWorkerRole(lambda: Work(users), clock=lambda: AT).replace(
            actor_user_id=ADMIN_ID,
            target_user_id=TARGET_ID,
            role=None,
            scope=None,
            authorization_version=1,
            section_ids=(ORG_ID,),
        )
    assert users.writes == 0
