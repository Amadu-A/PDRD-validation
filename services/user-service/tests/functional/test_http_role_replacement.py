# services/user-service/tests/functional/test_http_role_replacement.py

"""Проверяет закрытые контракты чтения и атомарной замены ролей."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.replace_role import RoleReplacement
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)
from pdrd_user_service.main import create_app

KEY = "test-internal-key-which-remains-private"
ADMIN_ID = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ROLE_ID = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class Ready:
    """Отвечает готовностью тестового контейнера."""

    async def is_ready(self) -> bool:
        """Подтверждает готовность."""
        return True


class Replacement:
    """Запоминает вызовы транспортного слоя и моделирует CAS."""

    def __init__(self) -> None:
        """Создаёт пустой след операций."""
        self.calls: list[tuple[str, dict[str, object]]] = []

    @staticmethod
    def result(version: int) -> RoleReplacement:
        """Собирает ответ с действующей ролью проектировщика."""
        user = UserAccount(
            user_id=USER_ID,
            kind=UserKind.CORPORATE,
            tier=AccessTier.MEMBER,
            status=UserStatus.ACTIVE,
            display_name="Проектировщик",
            login="designer.user",
            created_at=AT - timedelta(days=1),
            authorization_version=version,
        )
        assignment = RoleAssignment(
            assignment_id=ROLE_ID,
            user_id=USER_ID,
            role=Role.DESIGNER,
            source=RoleSource.LOCAL,
            scope=RoleScope(ScopeKind.OWN),
            created_at=AT - timedelta(hours=1),
        )
        return RoleReplacement(user, (Role.DESIGNER,), (assignment,))

    async def inspect(self, **values: object) -> RoleReplacement:
        """Возвращает текущие роли выбранного пользователя."""
        self.calls.append(("inspect", values))
        return self.result(2)

    async def replace(self, **values: object) -> RoleReplacement:
        """Отклоняет устаревший CAS и недопустимую админ-роль."""
        self.calls.append(("replace", values))
        if values["authorization_version"] != 2:
            raise AuthorizationConflict("Устарело")
        if values["role"] is Role.PLATFORM_ADMIN:
            raise ValueError("Администратора назначает bootstrap")
        return self.result(3)


def client_and_replacement() -> tuple[TestClient, Replacement]:
    """Создаёт приложение с управляемым сценарием ролей."""
    replacement = Replacement()
    settings = Settings(
        _env_file=None,
        enabled=True,
        internal_key=KEY,
        database=DatabaseSettings(password="private-test-password"),
    )

    async def close() -> None:
        """Закрывает тестовый контейнер."""

    container = ApplicationContainer(
        settings=settings,
        readiness=Ready(),
        directory=None,
        shutdown_callback=close,
        role_replacement=replacement,
    )
    return TestClient(create_app(container)), replacement


def test_role_detail_requires_service_key_and_actor() -> None:
    """Список действующих ролей не доступен браузеру напрямую."""
    client, replacement = client_and_replacement()
    url = f"/internal/v1/users/{USER_ID}/roles"
    with client:
        assert client.get(url).status_code == 401
        assert (
            client.get(url, headers={"Authorization": f"Bearer {KEY}"}).status_code
            == 422
        )
        response = client.get(
            url,
            headers={
                "Authorization": f"Bearer {KEY}",
                "X-PDRD-Actor-Id": str(ADMIN_ID),
            },
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["user"]["authorization_version"] == 2
    assert response.json()["roles"] == ["designer"]
    assert response.json()["assignments"][0]["scope"] == {
        "kind": "own",
        "organization_id": None,
        "department_id": None,
    }
    assert replacement.calls == [
        ("inspect", {"actor_user_id": ADMIN_ID, "target_user_id": USER_ID})
    ]


def test_patch_role_accepts_cas_and_rejects_client_authority_fields() -> None:
    """Клиент не задаёт источник роли, секрет и полномочия вне контракта."""
    client, replacement = client_and_replacement()
    url = f"/internal/v1/users/{USER_ID}/role"
    headers = {
        "Authorization": f"Bearer {KEY}",
        "X-PDRD-Actor-Id": str(ADMIN_ID),
    }
    payload = {
        "role": "designer",
        "scope": {"kind": "own"},
        "authorization_version": 2,
    }
    with client:
        for field in ("source", "password", "permissions"):
            assert (
                client.patch(
                    url, json={**payload, field: "injected"}, headers=headers
                ).status_code
                == 422
            )
        stale = client.patch(
            url, json={**payload, "authorization_version": 1}, headers=headers
        )
        admin = client.patch(
            url,
            json={
                **payload,
                "role": "platform_admin",
                "scope": {"kind": "platform"},
            },
            headers=headers,
        )
        valid = client.patch(url, json=payload, headers=headers)
    assert stale.status_code == 409
    assert admin.status_code == 422
    assert valid.status_code == 200
    assert valid.json()["user"]["authorization_version"] == 3
    assert [name for name, _ in replacement.calls] == [
        "replace",
        "replace",
        "replace",
    ]
