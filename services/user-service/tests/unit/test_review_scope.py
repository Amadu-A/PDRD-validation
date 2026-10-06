# services/user-service/tests/unit/test_review_scope.py

"""Проверяет доступ к чужому Review по текущим ролям и членству отдела."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_user_service.application.use_cases.review_scope import ReviewScopeAccess
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
ACTOR = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
OWNER = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ORG = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
DEP = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OTHER_DEP = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
ASSIGNMENT = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


def profile(user_id: UUID) -> UserAccount:
    """Создаёт активного участника без назначений."""
    return UserAccount(
        user_id=user_id,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Сотрудник",
        login=f"user-{user_id.hex}",
        created_at=AT - timedelta(days=1),
    )


def head_assignment(*, role: Role = Role.DEPARTMENT_HEAD) -> RoleAssignment:
    """Создаёт назначение руководителя либо платформенного администратора."""
    scope = (
        RoleScope(ScopeKind.PLATFORM)
        if role is Role.PLATFORM_ADMIN
        else RoleScope(ScopeKind.DEPARTMENT, ORG, DEP)
    )
    return RoleAssignment(
        assignment_id=ASSIGNMENT,
        user_id=ACTOR,
        role=role,
        source=RoleSource.LOCAL,
        scope=scope,
        created_at=AT - timedelta(hours=1),
    )


class Users:
    """Хранит текущие профили, назначения и членства без SQL."""

    def __init__(self) -> None:
        """Создаёт руководителя и автора в одном отделе."""
        self.profiles = {ACTOR: profile(ACTOR), OWNER: profile(OWNER)}
        self.assignments = (head_assignment(),)
        self.memberships = {
            ACTOR: (Membership(ACTOR, ORG, DEP),),
            OWNER: (Membership(OWNER, ORG, DEP),),
        }
        self.lock_order: list[UUID] = []

    async def get_user(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None:
        """Отмечает блокировку строки перед проверкой прав."""
        assert for_update
        self.lock_order.append(user_id)
        return self.profiles.get(user_id)

    async def list_assignments(
        self, user_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleAssignment, ...]:
        """Возвращает роль только актёра."""
        assert user_id == ACTOR and for_update
        return self.assignments

    async def list_memberships(self, user_id: UUID) -> tuple[Membership, ...]:
        """Возвращает членства с уже учтённой активностью справочника."""
        return self.memberships.get(user_id, ())


class Work:
    """Минимальный контекст транзакции для сценария чтения."""

    def __init__(self, users: Users) -> None:
        """Принимает память."""
        self.users = users

    async def __aenter__(self) -> "Work":
        """Открывает чтение."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Закрывает чтение."""
        del args


@pytest.mark.asyncio
async def test_head_reads_only_author_of_same_active_department() -> None:
    """Совпадение роли и обоих действующих членств обязательно."""
    users = Users()
    access = ReviewScopeAccess(lambda: Work(users), clock=lambda: AT)
    assert await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)
    assert users.lock_order == sorted((ACTOR, OWNER))

    users.memberships[OWNER] = (Membership(OWNER, ORG, OTHER_DEP),)
    assert not await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)
    users.memberships[OWNER] = (Membership(OWNER, ORG, DEP),)
    users.memberships[ACTOR] = (Membership(ACTOR, ORG, DEP, active=False),)
    assert not await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)


@pytest.mark.asyncio
async def test_expired_or_missing_role_and_blocked_profile_deny_review() -> None:
    """Роль, профиль и отдел проверяются при каждом запросе."""
    users = Users()
    access = ReviewScopeAccess(lambda: Work(users), clock=lambda: AT)
    users.assignments = (replace(head_assignment(), expires_at=AT),)
    assert not await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)
    users.assignments = ()
    assert not await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)
    users.assignments = (head_assignment(),)
    users.profiles[OWNER] = replace(users.profiles[OWNER], status=UserStatus.BLOCKED)
    assert not await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)
    users.profiles.pop(OWNER)
    assert not await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)


@pytest.mark.asyncio
async def test_platform_admin_can_read_active_member_across_departments() -> None:
    """Платформенная роль остаётся глобальной, а владелец должен быть активен."""
    users = Users()
    users.assignments = (head_assignment(role=Role.PLATFORM_ADMIN),)
    users.memberships[ACTOR] = ()
    users.memberships[OWNER] = (Membership(OWNER, ORG, OTHER_DEP),)
    access = ReviewScopeAccess(lambda: Work(users), clock=lambda: AT)
    assert await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)
    users.profiles[OWNER] = replace(users.profiles[OWNER], status=UserStatus.BLOCKED)
    assert not await access.can_read(actor_user_id=ACTOR, owner_user_id=OWNER)
