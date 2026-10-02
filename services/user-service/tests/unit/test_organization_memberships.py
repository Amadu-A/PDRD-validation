# services/user-service/tests/unit/test_organization_memberships.py

"""Проверяет справочники, область отдела и CAS членства без PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.organization_memberships import (
    DepartmentNotFound,
    MembershipNotFound,
    OrganizationMemberships,
)
from pdrd_user_service.application.use_cases.users import AdminRequired
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import (
    Department,
    Membership,
    Organization,
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
USER_ID = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ORG_ID = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
DEP_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OTHER_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")


def member(user_id: UUID) -> UserAccount:
    """Создаёт активного корпоративного участника."""
    return UserAccount(
        user_id=user_id,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Сотрудник",
        login=f"user-{user_id.hex}",
        created_at=AT - timedelta(days=1),
    )


def admin_assignment() -> RoleAssignment:
    """Создаёт действующую платформенную роль для тестового актёра."""
    return RoleAssignment(
        assignment_id=OTHER_ID,
        user_id=ADMIN_ID,
        role=Role.PLATFORM_ADMIN,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.PLATFORM),
        created_at=AT - timedelta(hours=1),
    )


class Users:
    """Память с теми же границами чтения и записи, что у репозитория."""

    def __init__(self) -> None:
        """Создаёт двух пользователей и пустые справочники."""
        self.profiles = {ADMIN_ID: member(ADMIN_ID), USER_ID: member(USER_ID)}
        self.admin_roles: tuple[RoleAssignment, ...] = (admin_assignment(),)
        self.organizations: dict[UUID, Organization] = {}
        self.departments: dict[UUID, Department] = {}
        self.memberships: dict[tuple[UUID, UUID, UUID], Membership] = {}
        self.writes = 0

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Возвращает профиль; обновление требует заблокированного чтения."""
        if user_id == ADMIN_ID or for_update:
            assert for_update
        return self.profiles.get(user_id)

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает права только текущего актёра."""
        assert user_id == ADMIN_ID and for_update
        return self.admin_roles

    async def list_organizations(
        self, *, limit: int, offset: int
    ) -> tuple[tuple[Organization, ...], int]:
        """Применяет ограниченную пагинацию."""
        ordered = sorted(self.organizations.values(), key=lambda value: value.name)
        return tuple(ordered[offset : offset + limit]), len(ordered)

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Читает организацию."""
        return self.organizations.get(organization_id)

    async def create_organization(self, organization: Organization) -> None:
        """Сохраняет организацию."""
        self.organizations[organization.organization_id] = organization
        self.writes += 1

    async def get_department(
        self, organization_id: UUID, department_id: UUID
    ) -> Department | None:
        """Не возвращает отдел другой организации."""
        department = self.departments.get(department_id)
        if department is None or department.organization_id != organization_id:
            return None
        return department

    async def list_departments(
        self, organization_id: UUID, *, limit: int, offset: int
    ) -> tuple[tuple[Department, ...], int]:
        """Фильтрует по организации до пагинации."""
        ordered = sorted(
            (
                value
                for value in self.departments.values()
                if value.organization_id == organization_id
            ),
            key=lambda value: value.name,
        )
        return tuple(ordered[offset : offset + limit]), len(ordered)

    async def create_department(self, department: Department) -> None:
        """Сохраняет отдел."""
        self.departments[department.department_id] = department
        self.writes += 1

    async def list_memberships(self, user_id: UUID) -> tuple[Membership, ...]:
        """Читает только членства выбранного пользователя."""
        return tuple(
            value for key, value in self.memberships.items() if key[0] == user_id
        )

    async def get_department_membership(
        self, user_id: UUID, organization_id: UUID, department_id: UUID
    ) -> Membership | None:
        """Находит точное членство."""
        return self.memberships.get((user_id, organization_id, department_id))

    async def set_department_membership(
        self,
        updated_user: UserAccount,
        membership: Membership,
        *,
        expected_authorization_version: int,
    ) -> None:
        """Эмулирует атомарный CAS и изменение состояния."""
        current = self.profiles[membership.user_id]
        if current.authorization_version != expected_authorization_version:
            raise AuthorizationConflict
        self.profiles[membership.user_id] = updated_user
        self.memberships[
            (membership.user_id, membership.organization_id, membership.department_id)
        ] = membership
        self.writes += 1


class Work:
    """Предоставляет память в транзакционном контексте."""

    def __init__(self, users: Users) -> None:
        """Принимает тестовый репозиторий."""
        self.users = users
        self.commits = 0

    async def __aenter__(self) -> "Work":
        """Открывает контекст."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Завершает контекст."""
        del args

    async def commit(self) -> None:
        """Подтверждает явный commit сценария."""
        self.commits += 1


def scenario(users: Users) -> OrganizationMemberships:
    """Подставляет управляемые UUID справочника."""
    ids = iter((ORG_ID, DEP_ID))
    return OrganizationMemberships(
        lambda: Work(users), clock=lambda: AT, new_id=lambda: next(ids)
    )


@pytest.mark.asyncio
async def test_only_current_admin_can_manage_catalog_and_memberships() -> None:
    """Отзыв платформенной роли закрывает даже чтение и не пишет БД."""
    users = Users()
    operations = scenario(users)
    users.admin_roles = ()
    with pytest.raises(AdminRequired):
        await operations.organizations(actor_user_id=ADMIN_ID, limit=50, offset=0)
    with pytest.raises(AdminRequired):
        await operations.create_organization(actor_user_id=ADMIN_ID, name="Компания")
    with pytest.raises(AdminRequired):
        await operations.memberships(actor_user_id=ADMIN_ID, target_user_id=USER_ID)
    assert users.writes == 0


@pytest.mark.asyncio
async def test_catalog_pages_and_parent_scope() -> None:
    """Организация и отдел создаются последовательно, список ограничен."""
    users = Users()
    operations = scenario(users)
    organization = await operations.create_organization(
        actor_user_id=ADMIN_ID, name="  НеоТерм  "
    )
    department = await operations.create_department(
        actor_user_id=ADMIN_ID, organization_id=ORG_ID, name="  Проектирование "
    )
    assert (organization.name, department.name) == ("НеоТерм", "Проектирование")
    org_page = await operations.organizations(actor_user_id=ADMIN_ID, limit=1, offset=0)
    dept_page = await operations.departments(
        actor_user_id=ADMIN_ID, organization_id=ORG_ID, limit=1, offset=0
    )
    assert (org_page.total, org_page.items) == (1, (organization,))
    assert (dept_page.total, dept_page.items) == (1, (department,))
    with pytest.raises(ValueError, match="страницы"):
        await operations.organizations(actor_user_id=ADMIN_ID, limit=101, offset=0)
    with pytest.raises(DepartmentNotFound):
        await operations.set_membership(
            actor_user_id=ADMIN_ID,
            target_user_id=USER_ID,
            organization_id=OTHER_ID,
            department_id=DEP_ID,
            active=True,
            authorization_version=1,
        )


@pytest.mark.asyncio
async def test_membership_changes_are_cas_guarded_and_idempotent() -> None:
    """Назначение и снятие меняют версию ровно по одному разу."""
    users = Users()
    operations = scenario(users)
    await operations.create_organization(actor_user_id=ADMIN_ID, name="НеоТерм")
    await operations.create_department(
        actor_user_id=ADMIN_ID, organization_id=ORG_ID, name="Проектирование"
    )
    with pytest.raises(MembershipNotFound):
        await operations.set_membership(
            actor_user_id=ADMIN_ID,
            target_user_id=USER_ID,
            organization_id=ORG_ID,
            department_id=DEP_ID,
            active=False,
            authorization_version=1,
        )
    created = await operations.set_membership(
        actor_user_id=ADMIN_ID,
        target_user_id=USER_ID,
        organization_id=ORG_ID,
        department_id=DEP_ID,
        active=True,
        authorization_version=1,
    )
    assert created.authorization_version == 2
    assert created.membership.active
    repeated = await operations.set_membership(
        actor_user_id=ADMIN_ID,
        target_user_id=USER_ID,
        organization_id=ORG_ID,
        department_id=DEP_ID,
        active=True,
        authorization_version=2,
    )
    assert repeated.authorization_version == 2
    with pytest.raises(AuthorizationConflict):
        await operations.set_membership(
            actor_user_id=ADMIN_ID,
            target_user_id=USER_ID,
            organization_id=ORG_ID,
            department_id=DEP_ID,
            active=False,
            authorization_version=1,
        )
    removed = await operations.set_membership(
        actor_user_id=ADMIN_ID,
        target_user_id=USER_ID,
        organization_id=ORG_ID,
        department_id=DEP_ID,
        active=False,
        authorization_version=2,
    )
    assert removed.authorization_version == 3
    assert not removed.membership.active
    assert (
        len(
            await operations.memberships(actor_user_id=ADMIN_ID, target_user_id=USER_ID)
        )
        == 1
    )
    assert users.profiles[USER_ID].authorization_version == 3
    assert users.writes == 4


@pytest.mark.asyncio
async def test_inactive_target_or_department_cannot_gain_membership() -> None:
    """Заблокированный пользователь и выключенный отдел не получают доступ."""
    users = Users()
    operations = scenario(users)
    await operations.create_organization(actor_user_id=ADMIN_ID, name="НеоТерм")
    await operations.create_department(
        actor_user_id=ADMIN_ID, organization_id=ORG_ID, name="Проектирование"
    )
    users.profiles[USER_ID] = replace(
        users.profiles[USER_ID], status=UserStatus.BLOCKED
    )
    with pytest.raises(ValueError, match="активному"):
        await operations.set_membership(
            actor_user_id=ADMIN_ID,
            target_user_id=USER_ID,
            organization_id=ORG_ID,
            department_id=DEP_ID,
            active=True,
            authorization_version=1,
        )
    users.profiles[USER_ID] = replace(users.profiles[USER_ID], status=UserStatus.ACTIVE)
    users.departments[DEP_ID] = replace(users.departments[DEP_ID], active=False)
    with pytest.raises(ValueError, match="активны"):
        await operations.set_membership(
            actor_user_id=ADMIN_ID,
            target_user_id=USER_ID,
            organization_id=ORG_ID,
            department_id=DEP_ID,
            active=True,
            authorization_version=1,
        )
