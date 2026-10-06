# services/user-service/tests/unit/test_user_listing.py

"""Проверяет роль администратора и границы страницы до чтения списка."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_user_service.application.use_cases.list_users import AdminUserListing
from pdrd_user_service.application.use_cases.users import AdminRequired
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)

AT = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
ADMIN_ID = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ROLE_ID = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")


def admin() -> UserAccount:
    """Создаёт активного сотрудника без предположения о его роли."""
    return UserAccount(
        user_id=ADMIN_ID,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Администратор",
        login="admin.user",
        created_at=AT - timedelta(days=1),
    )


def assignment() -> RoleAssignment:
    """Создаёт глобальную роль, которую каталог проверяет сам."""
    return RoleAssignment(
        assignment_id=ROLE_ID,
        user_id=ADMIN_ID,
        role=Role.PLATFORM_ADMIN,
        source=RoleSource.LOCAL,
        scope=RoleScope(ScopeKind.PLATFORM),
        created_at=AT - timedelta(hours=1),
    )


class Users:
    """Фиксирует, был ли список прочитан до проверки прав."""

    def __init__(self) -> None:
        """Создаёт действующего администратора и страницу профилей."""
        self.actor: UserAccount | None = admin()
        self.roles: tuple[RoleAssignment, ...] = (assignment(),)
        self.reads = 0
        self.target = replace(admin(), user_id=USER_ID)
        self.target_roles = ()

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Имитирует защищённое чтение роли из базы."""
        assert for_update
        return self.actor if user_id == ADMIN_ID else None

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает сохранённую роль указанного пользователя."""
        if user_id == ADMIN_ID:
            assert for_update
            return self.roles
        return self.target_roles

    async def list_memberships(self, user_id: UUID) -> tuple:
        """У тестовой страницы нет членства в отделах."""
        return ()

    async def list_users(
        self, *, limit: int, offset: int
    ) -> tuple[tuple[UserAccount, ...], int]:
        """Выдаёт страницу только после успешной проверки полномочий."""
        self.reads += 1
        assert (limit, offset) == (1, 1)
        return (self.target,), 2


class Work:
    """Предоставляет памяти тот же интерфейс транзакции."""

    def __init__(self, users: Users) -> None:
        """Принимает память каталога."""
        self.users = users

    async def __aenter__(self) -> "Work":
        """Открывает чтение."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Завершает чтение."""
        del args


@pytest.mark.asyncio
async def test_admin_user_page_returns_total_and_requested_window() -> None:
    """Действующий администратор получает только указанную страницу."""
    users = Users()
    listing = AdminUserListing(lambda: Work(users), clock=lambda: AT)
    page = await listing.page(actor_user_id=ADMIN_ID, limit=1, offset=1)
    assert (page.total, page.limit, page.offset) == (2, 1, 1)
    assert len(page.items) == 1
    assert page.items[0].user_id == USER_ID
    assert users.reads == 1


@pytest.mark.asyncio
async def test_non_admin_expired_or_blocked_cannot_enumerate_users() -> None:
    """Отсутствие роли, истечение срока и блокировка закрывают список."""
    users = Users()
    listing = AdminUserListing(lambda: Work(users), clock=lambda: AT)
    for actor, roles in (
        (None, ()),
        (admin(), ()),
        (admin(), (replace(assignment(), expires_at=AT),)),
        (replace(admin(), status=UserStatus.BLOCKED), (assignment(),)),
    ):
        users.actor, users.roles = actor, roles
        with pytest.raises(AdminRequired):
            await listing.page(actor_user_id=ADMIN_ID, limit=1, offset=1)
    assert users.reads == 0


@pytest.mark.asyncio
async def test_page_bounds_are_checked_before_database_read() -> None:
    """Неприемлемая пагинация не обращается к хранилищу."""
    users = Users()
    listing = AdminUserListing(lambda: Work(users), clock=lambda: AT)
    for limit, offset in ((0, 0), (101, 0), (50, -1)):
        with pytest.raises(ValueError, match="страницы"):
            await listing.page(actor_user_id=ADMIN_ID, limit=limit, offset=offset)
    assert users.reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role, enabled, allowed, automatic, editable",
    [
        (Role.DESIGNER, False, False, False, True),
        (Role.DESIGNER, True, True, False, True),
        (Role.DEPARTMENT_HEAD, False, True, True, False),
        (Role.PLATFORM_ADMIN, False, True, True, False),
    ],
)
async def test_user_listing_exposes_effective_review_state(
    role, enabled, allowed, automatic, editable
):
    """Строка админки показывает действующее право, а не только ручной флаг."""
    users = Users()
    users.target = replace(users.target, review_access_enabled=enabled)
    scope = {
        Role.DESIGNER: ScopeKind.OWN,
        Role.DEPARTMENT_HEAD: ScopeKind.SECTIONS,
        Role.PLATFORM_ADMIN: ScopeKind.PLATFORM,
    }[role]
    users.target_roles = (
        replace(assignment(), user_id=USER_ID, role=role, scope=RoleScope(scope)),
    )
    page = await AdminUserListing(lambda: Work(users), clock=lambda: AT).page(
        actor_user_id=ADMIN_ID, limit=1, offset=1
    )
    state = page.review_access_states[0]
    assert (
        state.review_access,
        state.review_access_automatic,
        state.review_access_editable,
    ) == (allowed, automatic, editable)
