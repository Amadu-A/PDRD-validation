# services/admin-service/tests/functional/test_internal_clients.py

"""Проверяет реальные HTTP контракты с auth-service и user-service без сети."""

import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pdrd_admin_service.application.admin_users import (
    AuthenticationRequired,
    RoleConflict,
    UpstreamUnavailable,
)
from pdrd_admin_service.contracts.models import ReplaceRoleRequest
from pdrd_admin_service.infrastructure.clients import (
    AuthServiceClient,
    UserServiceClient,
)

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_ID = UUID("22222222-2222-4222-8222-222222222222")
AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC).isoformat()
CSRF = "csrf-token-which-has-at-least-32-characters"
KEY = "internal-service-key-private-test-value"


def profile() -> dict[str, object]:
    """Создаёт точный внутренний ответ user-service."""
    return {
        "user_id": str(TARGET_ID),
        "kind": "corporate",
        "tier": "member",
        "status": "active",
        "display_name": "Сотрудник",
        "login": "designer",
        "email": None,
        "created_at": AT,
        "last_login_at": None,
        "authorization_version": 3,
    }


@pytest.mark.asyncio
async def test_introspect_uses_only_internal_key_and_token_body() -> None:
    """Cookie не уходит в URL, заголовок браузера или user-service."""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Проверяет точные поля доверенного вызова."""
        requests.append(request)
        assert request.url.path == "/internal/v1/auth/introspect"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert "cookie" not in request.headers
        assert json.loads(request.content) == {"token": "raw-browser-token"}
        return httpx.Response(
            200,
            json={
                "user_id": str(ACTOR_ID),
                "permissions": ["admin.access"],
                "csrf_token": CSRF,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://auth-service:8000"
    ) as client:
        result = await AuthServiceClient(client, KEY).introspect("raw-browser-token")
    assert result.user_id == ACTOR_ID
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_introspect_fails_closed_on_invalid_or_malformed_response() -> None:
    """Отозванная сессия и нарушенный внутренний контракт не дают доступа."""

    def invalid(_: httpx.Request) -> httpx.Response:
        """Моделирует истёкшую сессию."""
        return httpx.Response(401)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(invalid), base_url="http://auth-service:8000"
    ) as client:
        with pytest.raises(AuthenticationRequired):
            await AuthServiceClient(client, KEY).introspect("expired")

    for status_code in (403, 404):

        def rejected(_: httpx.Request, code: int = status_code) -> httpx.Response:
            """Моделирует неверный служебный ключ или отсутствующий маршрут."""
            return httpx.Response(code)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(rejected),
            base_url="http://auth-service:8000",
        ) as client:
            with pytest.raises(UpstreamUnavailable):
                await AuthServiceClient(client, KEY).introspect("raw-browser-token")

    def malformed(_: httpx.Request) -> httpx.Response:
        """Моделирует несоответствие версии соседнего сервиса."""
        return httpx.Response(200, json={"user_id": str(ACTOR_ID)})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(malformed), base_url="http://auth-service:8000"
    ) as client:
        with pytest.raises(UpstreamUnavailable):
            await AuthServiceClient(client, KEY).introspect("raw-browser-token")


@pytest.mark.asyncio
async def test_list_and_role_detail_send_actor_from_verified_session() -> None:
    """Закрытые чтения передают служебный ключ и UUID текущего актёра."""
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Проверяет параметры списка и заголовки карточки."""
        paths.append(request.url.path)
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["x-pdrd-actor-id"] == str(ACTOR_ID)
        if request.url.path == "/internal/v1/users":
            assert dict(request.url.params) == {"limit": "20", "offset": "40"}
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            **profile(),
                            "review_access": False,
                            "review_access_automatic": False,
                            "review_access_editable": True,
                        }
                    ],
                    "total": 1,
                    "limit": 20,
                    "offset": 40,
                },
            )
        assert request.url.path == f"/internal/v1/users/{TARGET_ID}/roles"
        return httpx.Response(
            200,
            json={"user": profile(), "roles": ["designer"], "assignments": []},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://user-service:8000"
    ) as client:
        directory = UserServiceClient(client, KEY)
        page = await directory.list_users(ACTOR_ID, limit=20, offset=40)
        detail = await directory.get_roles(ACTOR_ID, TARGET_ID)
    assert page.items[0].user_id == TARGET_ID
    assert detail.roles == ("designer",)
    assert paths == [
        "/internal/v1/users",
        f"/internal/v1/users/{TARGET_ID}/roles",
    ]


@pytest.mark.asyncio
async def test_atomic_patch_forwards_cas_and_maps_conflict() -> None:
    """Ожидаемая версия доходит до user-service, stale version остаётся 409."""

    def respond(request: httpx.Request) -> httpx.Response:
        """Проверяет доверенный PATCH без пропуска browser actor header."""
        assert request.method == "PATCH"
        assert request.url.path == f"/internal/v1/users/{TARGET_ID}/role"
        assert request.headers["x-pdrd-actor-id"] == str(ACTOR_ID)
        assert json.loads(request.content) == {
            "role": "designer",
            "scope": {
                "kind": "own",
                "organization_id": None,
                "department_id": None,
            },
            "authorization_version": 3,
        }
        return httpx.Response(409)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://user-service:8000"
    ) as client:
        command = ReplaceRoleRequest(
            role="designer", scope={"kind": "own"}, authorization_version=3
        )
        with pytest.raises(RoleConflict):
            await UserServiceClient(client, KEY).replace_role(
                ACTOR_ID, TARGET_ID, command
            )


@pytest.mark.asyncio
async def test_organization_and_membership_client_preserves_actor_and_version() -> None:
    """Весь новый контракт идёт через закрытый API с проверенным UUID актёра."""
    organization_id = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
    department_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    seen: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Возвращает реальные формы ответов User Service для каждого маршрута."""
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["x-pdrd-actor-id"] == str(ACTOR_ID)
        seen.append((request.method, request.url.path))
        if request.url.path == "/internal/v1/organizations":
            if request.method == "GET":
                assert dict(request.url.params) == {"limit": "10", "offset": "5"}
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "organization_id": str(organization_id),
                                "name": "НеоТерм",
                                "active": True,
                            }
                        ],
                        "total": 1,
                        "limit": 10,
                        "offset": 5,
                    },
                )
            assert json.loads(request.content) == {"name": "НеоТерм"}
            return httpx.Response(
                201,
                json={
                    "organization_id": str(organization_id),
                    "name": "НеоТерм",
                    "active": True,
                },
            )
        if (
            request.url.path
            == f"/internal/v1/organizations/{organization_id}/departments"
        ):
            department = {
                "department_id": str(department_id),
                "organization_id": str(organization_id),
                "name": "Проектирование",
                "active": True,
            }
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"items": [department], "total": 1, "limit": 10, "offset": 0},
                )
            assert json.loads(request.content) == {"name": "Проектирование"}
            return httpx.Response(201, json=department)
        if request.url.path == f"/internal/v1/users/{TARGET_ID}/memberships":
            return httpx.Response(
                200,
                json=[
                    {
                        "user_id": str(TARGET_ID),
                        "organization_id": str(organization_id),
                        "department_id": str(department_id),
                        "active": True,
                    }
                ],
            )
        assert request.url.path == (
            f"/internal/v1/users/{TARGET_ID}/memberships/"
            f"{organization_id}/{department_id}"
        )
        assert json.loads(request.content) == {"authorization_version": 3}
        return httpx.Response(
            200,
            json={
                "user_id": str(TARGET_ID),
                "authorization_version": 4,
                "membership": {
                    "user_id": str(TARGET_ID),
                    "organization_id": str(organization_id),
                    "department_id": str(department_id),
                    "active": request.method == "PUT",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://user-service:8000"
    ) as client:
        directory = UserServiceClient(client, KEY)
        organizations = await directory.list_organizations(ACTOR_ID, limit=10, offset=5)
        created_organization = await directory.create_organization(
            ACTOR_ID, name="НеоТерм"
        )
        departments = await directory.list_departments(
            ACTOR_ID, organization_id, limit=10, offset=0
        )
        created_department = await directory.create_department(
            ACTOR_ID, organization_id, name="Проектирование"
        )
        memberships = await directory.list_memberships(ACTOR_ID, TARGET_ID)
        activated = await directory.set_membership(
            ACTOR_ID,
            TARGET_ID,
            organization_id,
            department_id,
            active=True,
            authorization_version=3,
        )
        deactivated = await directory.set_membership(
            ACTOR_ID,
            TARGET_ID,
            organization_id,
            department_id,
            active=False,
            authorization_version=3,
        )
    assert organizations.items[0].organization_id == organization_id
    assert created_organization.organization_id == organization_id
    assert departments.items[0].department_id == department_id
    assert created_department.department_id == department_id
    assert memberships[0].department_id == department_id
    assert activated.authorization_version == deactivated.authorization_version == 4
    assert activated.membership.active and not deactivated.membership.active
    assert len(seen) == 7


@pytest.mark.asyncio
async def test_review_access_client_forwards_only_command_and_trusted_actor():
    """Назначение идёт через приватный HTTP API со служебным ключом и строгим ответом."""
    import json

    from pdrd_admin_service.contracts.review_access_models import (
        ChangeReviewAccessRequest,
    )

    def respond(request):
        """Проверяет контракт запроса к владельцу профилей."""
        assert request.method == "PATCH"
        assert request.url.path == f"/internal/v1/users/{TARGET_ID}/review-access"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["x-pdrd-actor-id"] == str(ACTOR_ID)
        assert json.loads(request.content) == {
            "enabled": True,
            "authorization_version": 3,
        }
        return httpx.Response(
            200,
            json={
                "user": {**profile(), "authorization_version": 4},
                "review_access": True,
                "review_access_automatic": False,
                "review_access_editable": True,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://user-service:8000"
    ) as client:
        result = await UserServiceClient(client, KEY).change_review_access(
            ACTOR_ID,
            TARGET_ID,
            ChangeReviewAccessRequest(enabled=True, authorization_version=3),
        )
    assert result.review_access
    assert result.user.authorization_version == 4
