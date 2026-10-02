# services/user-service/tests/functional/test_http_user_listing.py

"""Проверяет защиту и пагинацию внутреннего списка для Admin Service."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_user_service.application.use_cases.list_users import UserPage
from pdrd_user_service.application.use_cases.users import AdminRequired
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.access import AccessTier
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.main import create_app

KEY = "test-internal-key-which-remains-private"
ADMIN_ID = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")


class Ready:
    """Реализует минимальный тестовый readiness-порт."""

    async def is_ready(self) -> bool:
        """Отвечает готовностью."""
        return True


class Listing:
    """Отслеживает переданные параметры, не открывая PostgreSQL."""

    def __init__(self) -> None:
        """Создаёт пустой след запросов."""
        self.calls: list[dict[str, object]] = []
        self.deny = False

    async def page(self, **values: object) -> UserPage:
        """Возвращает страницу либо имитирует отказ проверки роли."""
        self.calls.append(values)
        if self.deny:
            raise AdminRequired("Роль снята")
        user = UserAccount(
            user_id=USER_ID,
            kind=UserKind.EXTERNAL,
            tier=AccessTier.REGISTERED_FREE,
            status=UserStatus.ACTIVE,
            display_name="Клиент",
            email="client@example.test",
            created_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        )
        return UserPage((user,), 23, values["limit"], values["offset"])


def client_and_listing() -> tuple[TestClient, Listing]:
    """Создаёт включённый сервис с подменённым прикладным сценарием."""
    listing = Listing()
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
        user_listing=listing,
    )
    return TestClient(create_app(container)), listing


def test_user_listing_requires_key_actor_and_valid_page() -> None:
    """Ни браузер, ни служебный ключ без UUID администратора не читают список."""
    client, listing = client_and_listing()
    url = "/internal/v1/users?limit=1&offset=4"
    headers = {
        "Authorization": f"Bearer {KEY}",
        "X-PDRD-Actor-Id": str(ADMIN_ID),
    }
    with client:
        assert client.get(url).status_code == 401
        assert (
            client.get(url, headers={"Authorization": f"Bearer {KEY}"}).status_code
            == 422
        )
        assert (
            client.get("/internal/v1/users?limit=101", headers=headers).status_code
            == 422
        )
        assert (
            client.get("/internal/v1/users?offset=-1", headers=headers).status_code
            == 422
        )
        response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["total"] == 23
    assert response.json()["limit"] == 1
    assert response.json()["offset"] == 4
    assert response.json()["items"][0]["user_id"] == str(USER_ID)
    assert "password" not in response.text
    assert listing.calls == [{"actor_user_id": ADMIN_ID, "limit": 1, "offset": 4}]


def test_user_listing_fails_closed_when_admin_role_is_revoked() -> None:
    """Сервисный ключ и заголовок UUID сами по себе не выдают список."""
    client, listing = client_and_listing()
    listing.deny = True
    with client:
        response = client.get(
            "/internal/v1/users",
            headers={
                "Authorization": f"Bearer {KEY}",
                "X-PDRD-Actor-Id": str(ADMIN_ID),
            },
        )
    assert response.status_code == 403
    assert listing.calls == [{"actor_user_id": ADMIN_ID, "limit": 50, "offset": 0}]
