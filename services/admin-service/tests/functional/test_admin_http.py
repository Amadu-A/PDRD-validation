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
        from pdrd_admin_service.contracts.models import UserPage

        self.calls.append(("list", actor_user_id))
        return UserPage(items=(self.profile(),), total=1, limit=limit, offset=offset)

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


def make_app(*, enabled: bool = True) -> tuple[object, Sessions, Users]:
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
        AdminUsers(sessions, users) if enabled else None,
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
