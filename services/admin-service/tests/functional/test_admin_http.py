# services/admin-service/tests/functional/test_admin_http.py

"""Проверяет HTTP защиту административных маршрутов без внешней сети."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_admin_service.application.admin_users import AdminUsers
from pdrd_admin_service.core.container import ApplicationContainer
from pdrd_admin_service.core.settings import Settings
from pdrd_admin_service.main import create_app

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_ID = UUID("22222222-2222-4222-8222-222222222222")
AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC).isoformat()
CSRF = "csrf-token-which-has-at-least-32-characters"
KEY = "internal-key-longer-than-thirty-two-characters"


class Sessions:
    """Замена проверяющего auth-service."""

    def __init__(self) -> None:
        """По умолчанию разрешает права администратора."""
        self.calls = 0
        self.permissions = ("admin.access", "users.roles.assign")

    async def introspect(self, token: str) -> object:
        """Фиксирует проверку cookie и отдаёт один безопасный контекст."""
        from pdrd_admin_service.contracts.models import SessionIdentity

        assert token == "browser-secret"
        self.calls += 1
        return SessionIdentity(
            user_id=ACTOR_ID, permissions=self.permissions, csrf_token=CSRF
        )


class Users:
    """Замена user-service, фиксирующая повторную передачу актёра."""

    def __init__(self) -> None:
        """Создаёт список вызовов."""
        self.calls: list[tuple[str, object]] = []

    async def list_users(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> object:
        """Возвращает страницу."""
        from pdrd_admin_service.contracts.models import AdminUserResponse, UserPage

        self.calls.append(("list", actor_user_id))
        return UserPage(
            items=(
                AdminUserResponse(
                    **self.profile().model_dump(),
                    review_access=False,
                    review_access_automatic=False,
                    review_access_editable=True,
                ),
            ),
            total=1,
            limit=limit,
            offset=offset,
        )

    def profile(self) -> object:
        """Возвращает профиль без секрета."""
        from pdrd_admin_service.contracts.models import UserResponse

        return UserResponse(
            user_id=TARGET_ID,
            kind="corporate",
            tier="member",
            status="active",
            display_name="Иван Мейн",
            login="i.mein",
            email=None,
            created_at=AT,
            last_login_at=None,
            authorization_version=1,
        )

    async def get_roles(self, actor_user_id: UUID, target_user_id: UUID) -> object:
        """Отдаёт назначение для карточки пользователя."""
        from pdrd_admin_service.contracts.models import RoleDetailResponse

        self.calls.append(("roles", (actor_user_id, target_user_id)))
        return RoleDetailResponse(
            user=self.profile(), roles=("designer",), assignments=()
        )

    async def replace_role(
        self, actor_user_id: UUID, target_user_id: UUID, command: object
    ) -> object:
        """Фиксирует ожидаемую версию атомарного изменения."""
        from pdrd_admin_service.contracts.models import RoleDetailResponse

        self.calls.append(("replace", (actor_user_id, target_user_id, command)))
        return RoleDetailResponse(
            user=self.profile(), roles=("department_head",), assignments=()
        )

    async def change_review_access(self, actor_user_id, target_user_id, command):
        """Фиксирует актёра из сессии и отдельную строгую команду."""
        from pdrd_admin_service.contracts.review_access_models import (
            ReviewAccessChangeResponse,
        )

        self.calls.append(("review-access", (actor_user_id, target_user_id, command)))
        profile = self.profile().model_copy(update={"authorization_version": 2})
        return ReviewAccessChangeResponse(
            user=profile,
            review_access=command.enabled,
            review_access_automatic=False,
            review_access_editable=True,
        )


def make_app(
    *, enabled: bool = True, sections: object = None
) -> tuple[object, Sessions, Users]:
    """Создаёт приложение без сети и с явно установленными ключами."""
    sessions, users = Sessions(), Users()
    settings = Settings(
        _env_file=None,
        enabled=enabled,
        auth_service_internal_key=KEY if enabled else "",
        user_service_internal_key=KEY if enabled else "",
    )

    async def ready() -> bool:
        """Возвращает состояние зависимостей."""
        return enabled

    async def close() -> None:
        """Закрывает пустой тестовый контейнер."""

    container = ApplicationContainer(
        settings,
        AdminUsers(sessions, users, sections) if enabled else None,
        ready,
        close,
    )
    return create_app(container), sessions, users


def test_disabled_service_exposes_only_health() -> None:
    """Без конфигурации каталог не публикуется."""
    app, _, users = make_app(enabled=False)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.get("/api/v1/admin/users").status_code == 404
        assert client.get("/docs").status_code == 404
    assert users.calls == []


def test_cookie_and_permissions_protect_reads() -> None:
    """Без cookie либо admin.access нельзя читать список и карточку."""
    app, sessions, users = make_app()
    with TestClient(app) as client:
        missing = client.get("/api/v1/admin/users")
        assert missing.status_code == 401
        assert missing.headers["cache-control"] == "no-store"
        client.cookies.set("pdrd_session", "browser-secret")
        listed = client.get("/api/v1/admin/users?limit=20&offset=0")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["display_name"] == "Иван Мейн"
        assert "password" not in listed.text
        assert client.get(f"/api/v1/admin/users/{TARGET_ID}").status_code == 200
        sessions.permissions = ()
        assert client.get("/api/v1/admin/users").status_code == 403
    assert sessions.calls == 3
    assert users.calls == [("list", ACTOR_ID), ("roles", (ACTOR_ID, TARGET_ID))]


def test_invalid_pagination_and_role_payload_rejected() -> None:
    """Не допускает неограниченный список и клиентский источник роли."""
    app, _, users = make_app()
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "browser-secret")
        assert client.get("/api/v1/admin/users?limit=1000").status_code == 422
        response = client.patch(
            f"/api/v1/admin/users/{TARGET_ID}/role",
            headers={"X-CSRF-Token": CSRF},
            json={
                "role": "platform_admin",
                "scope": {"kind": "platform"},
                "source": "local",
                "authorization_version": 1,
            },
        )
        assert response.status_code == 422
    assert users.calls == []


def test_role_card_and_atomic_patch_follow_csrf_and_version_contract() -> None:
    """UI читает роль и меняет её только с CSRF и версией полномочий."""
    app, _, users = make_app()
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "browser-secret")
        path = f"/api/v1/admin/users/{TARGET_ID}"
        detail = client.get(f"{path}/roles")
        assert detail.status_code == 200
        assert detail.json()["roles"] == ["designer"]
        payload = {
            "role": "department_head",
            "scope": {
                "kind": "department",
                "organization_id": str(ACTOR_ID),
                "department_id": str(TARGET_ID),
            },
            "authorization_version": 7,
        }
        assert client.patch(f"{path}/role", json=payload).status_code == 403
        assert (
            client.patch(
                f"{path}/role",
                json=payload,
                headers={"X-CSRF-Token": "wrong", "X-PDRD-Actor-Id": str(TARGET_ID)},
            ).status_code
            == 403
        )
        result = client.patch(
            f"{path}/role",
            json=payload,
            headers={"X-CSRF-Token": CSRF, "X-PDRD-Actor-Id": str(TARGET_ID)},
        )
        assert result.status_code == 200
        assert result.json()["roles"] == ["department_head"]
        assert users.calls[1][1][0] == ACTOR_ID
        assert users.calls[1][1][2].authorization_version == 7


def test_platform_admin_assignment_preserves_actor_and_csrf() -> None:
    """Назначение администратора проходит тот же защищённый API и версию."""
    app, _, users = make_app()
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "browser-secret")
        payload = {
            "role": "platform_admin",
            "scope": {"kind": "platform"},
            "authorization_version": 3,
        }
        path = f"/api/v1/admin/users/{TARGET_ID}/role"
        assert client.patch(path, json=payload).status_code == 403
        response = client.patch(path, json=payload, headers={"X-CSRF-Token": CSRF})
        assert response.status_code == 200
    action, (actor, target, command) = users.calls[0]
    assert (action, actor, target) == ("replace", ACTOR_ID, TARGET_ID)
    assert command.role == "platform_admin"
    assert command.authorization_version == 3


def test_section_catalog_and_multi_assignment_require_admin_csrf_and_known_ids() -> (
    None
):
    """Живой каталог не дублируется, а атомарная команда не принимает чужого актёра."""
    from pdrd_admin_service.contracts.section_models import CatalogSection

    class Sections:
        """Имитирует два раздела главной страницы из Knowledge Service."""

        async def list_sections(self) -> tuple:
            """Возвращает актуальный список UUID и имён."""
            return (
                CatalogSection(section_id=ACTOR_ID, name="ЭОМ"),
                CatalogSection(section_id=TARGET_ID, name="ОВ"),
            )

    app, sessions, users = make_app(sections=Sections())
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/users/section-catalog").status_code == 401
        client.cookies.set("pdrd_session", "browser-secret")
        catalog = client.get("/api/v1/admin/users/section-catalog")
        assert catalog.status_code == 200
        assert [item["name"] for item in catalog.json()] == ["ЭОМ", "ОВ"]
        command = {
            "role": "department_head",
            "scope": {"kind": "sections"},
            "authorization_version": 1,
            "section_ids": [str(ACTOR_ID), str(TARGET_ID)],
        }
        path = f"/api/v1/admin/users/{TARGET_ID}/role"
        assert client.patch(path, json=command).status_code == 403
        headers = {"X-CSRF-Token": CSRF, "X-PDRD-Actor-Id": str(TARGET_ID)}
        assert client.patch(path, json=command, headers=headers).status_code == 200
        assert users.calls[-1][1][0] == ACTOR_ID
        assert users.calls[-1][1][2].section_ids == (ACTOR_ID, TARGET_ID)
        writes = len(users.calls)
        command["section_ids"] = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
        assert client.patch(path, json=command, headers=headers).status_code == 422
        assert len(users.calls) == writes
        sessions.permissions = ()
        assert client.get("/api/v1/admin/users/section-catalog").status_code == 403


def test_review_access_patch_requires_admin_csrf_and_ignores_spoofed_actor():
    """Только администратор с CSRF меняет ревью; браузерный UUID актёра игнорируется."""
    app, sessions, users = make_app()
    path = f"/api/v1/admin/users/{TARGET_ID}/review-access"
    payload = {"enabled": True, "authorization_version": 1}
    with TestClient(app) as client:
        assert client.patch(path, json=payload).status_code == 401
        client.cookies.set("pdrd_session", "browser-secret")
        assert client.patch(path, json=payload).status_code == 403
        assert (
            client.patch(
                path, json=payload, headers={"X-CSRF-Token": "wrong"}
            ).status_code
            == 403
        )
        for field, value in (
            ("enabled", "true"),
            ("authorization_version", True),
            ("role", "platform_admin"),
        ):
            assert (
                client.patch(
                    path, json={**payload, field: value}, headers={"X-CSRF-Token": CSRF}
                ).status_code
                == 422
            )
        response = client.patch(
            path,
            json=payload,
            headers={"X-CSRF-Token": CSRF, "X-PDRD-Actor-Id": str(TARGET_ID)},
        )
        assert response.status_code == 200
        assert response.json()["review_access"]
        assert response.json()["user"]["authorization_version"] == 2
        sessions.permissions = (
            "review.gold.create",
            "review.findings.decide",
            "experience.capture",
        )
        assert (
            client.patch(path, json=payload, headers={"X-CSRF-Token": CSRF}).status_code
            == 403
        )
    assert len(users.calls) == 1
    operation, (actor, target, command) = users.calls[0]
    assert (operation, actor, target, command.enabled) == (
        "review-access",
        ACTOR_ID,
        TARGET_ID,
        True,
    )
