# services/admin-service/tests/unit/test_admin_users.py

"""Проверяет запреты и передачу доверенного UUID в административном use case."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_admin_service.application.admin_users import (
    AdminUsers,
    AuthenticationRequired,
    CsrfRejected,
    PermissionDenied,
    UpstreamUnavailable,
)
from pdrd_admin_service.contracts.models import (
    AdminUserResponse,
    ReplaceRoleRequest,
    RoleDetailResponse,
    SessionIdentity,
    UserPage,
    UserResponse,
)

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_ID = UUID("22222222-2222-4222-8222-222222222222")
AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CSRF = "csrf-token-which-has-at-least-32-characters"


def user() -> UserResponse:
    """Создаёт профиль без каких-либо секретов."""
    return UserResponse(
        user_id=TARGET_ID,
        kind="corporate",
        tier="member",
        status="active",
        display_name="Проектировщик",
        login="designer",
        email=None,
        created_at=AT,
        last_login_at=None,
        authorization_version=1,
    )


class Sessions:
    """Возвращает управляемый снимок auth-service."""

    def __init__(self, permissions: tuple[str, ...]) -> None:
        """Фиксирует доступные операции."""
        self.permissions = permissions
        self.tokens: list[str] = []
        self.fail = False

    async def introspect(self, token: str) -> SessionIdentity:
        """Учитывает повторную проверку cookie и возможный отказ auth."""
        self.tokens.append(token)
        if self.fail:
            raise UpstreamUnavailable
        return SessionIdentity(
            user_id=ACTOR_ID, permissions=self.permissions, csrf_token=CSRF
        )


class Users:
    """Запоминает только разрешённые вызовы каталога."""

    def __init__(self) -> None:
        """Создаёт пустой след."""
        self.calls: list[tuple[str, object]] = []

    async def list_users(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> UserPage:
        """Фиксирует, что актёр пришёл из auth, а не из клиента."""
        self.calls.append(("list", (actor_user_id, limit, offset)))
        return UserPage(
            items=(
                AdminUserResponse(
                    **user().model_dump(),
                    review_access=False,
                    review_access_automatic=False,
                    review_access_editable=True,
                ),
            ),
            total=1,
            limit=limit,
            offset=offset,
        )

    async def get_roles(
        self, actor_user_id: UUID, target_user_id: UUID
    ) -> RoleDetailResponse:
        """Отдаёт текущие роли из другого сервиса."""
        self.calls.append(("roles", (actor_user_id, target_user_id)))
        return RoleDetailResponse(user=user(), roles=("designer",), assignments=())

    async def replace_role(
        self, actor_user_id: UUID, target_user_id: UUID, command: ReplaceRoleRequest
    ) -> RoleDetailResponse:
        """Фиксирует CAS и не исполняет прямой SQL."""
        self.calls.append(("replace", (actor_user_id, target_user_id, command)))
        return RoleDetailResponse(
            user=user(), roles=("department_head",), assignments=()
        )


@pytest.mark.asyncio
async def test_missing_cookie_rejected_before_internal_api() -> None:
    """Анонимный браузер не заставляет admin читать каталог."""
    sessions, users = Sessions(("admin.access",)), Users()
    with pytest.raises(AuthenticationRequired):
        await AdminUsers(sessions, users).list_users(None, limit=50, offset=0)
    assert sessions.tokens == []
    assert users.calls == []


@pytest.mark.asyncio
async def test_current_permission_checked_for_every_read() -> None:
    """После отзыва admin.access следующий запрос уже отклоняется."""
    sessions, users = Sessions(("admin.access",)), Users()
    admin = AdminUsers(sessions, users)
    page = await admin.list_users("raw-token", limit=20, offset=40)
    assert page.total == 1
    assert users.calls == [("list", (ACTOR_ID, 20, 40))]

    sessions.permissions = ()
    with pytest.raises(PermissionDenied):
        await admin.get_user("raw-token", TARGET_ID)
    assert sessions.tokens == ["raw-token", "raw-token"]
    assert len(users.calls) == 1


@pytest.mark.asyncio
async def test_mutation_requires_role_permission_and_matching_csrf() -> None:
    """Cookie без права либо без CSRF не достигает user-service."""
    sessions, users = Sessions(("admin.access",)), Users()
    command = ReplaceRoleRequest(
        role="designer", scope={"kind": "own"}, authorization_version=1
    )
    admin = AdminUsers(sessions, users)
    with pytest.raises(PermissionDenied):
        await admin.replace_role("raw-token", TARGET_ID, command, csrf=CSRF)

    sessions.permissions = ("users.roles.assign",)
    with pytest.raises(CsrfRejected):
        await admin.replace_role("raw-token", TARGET_ID, command, csrf=None)
    with pytest.raises(CsrfRejected):
        await admin.replace_role("raw-token", TARGET_ID, command, csrf="wrong")
    assert users.calls == []

    result = await admin.replace_role("raw-token", TARGET_ID, command, csrf=CSRF)
    assert result.roles == ("department_head",)
    assert users.calls == [("replace", (ACTOR_ID, TARGET_ID, command))]


@pytest.mark.asyncio
async def test_auth_outage_fails_closed_before_role_replace() -> None:
    """Недоступный auth-service запрещает даже ранее авторизованного актёра."""
    sessions, users = Sessions(("users.roles.assign",)), Users()
    sessions.fail = True
    with pytest.raises(UpstreamUnavailable):
        await AdminUsers(sessions, users).replace_role(
            "raw-token",
            TARGET_ID,
            ReplaceRoleRequest(
                role="designer", scope={"kind": "own"}, authorization_version=1
            ),
            csrf=CSRF,
        )
    assert users.calls == []


@pytest.mark.asyncio
async def test_role_detail_and_cas_replace_use_trusted_actor() -> None:
    """Карточка и атомарная замена используют UUID сессии и версию клиента."""
    sessions, users = Sessions(("admin.access", "users.roles.assign")), Users()
    admin = AdminUsers(sessions, users)
    detail = await admin.get_roles("raw-token", TARGET_ID)
    assert detail.roles == ("designer",)
    command = ReplaceRoleRequest(
        role="department_head",
        scope={
            "kind": "department",
            "organization_id": ACTOR_ID,
            "department_id": TARGET_ID,
        },
        authorization_version=7,
    )
    replaced = await admin.replace_role("raw-token", TARGET_ID, command, csrf=CSRF)
    assert replaced.roles == ("department_head",)
    assert users.calls == [
        ("roles", (ACTOR_ID, TARGET_ID)),
        ("replace", (ACTOR_ID, TARGET_ID, command)),
    ]
