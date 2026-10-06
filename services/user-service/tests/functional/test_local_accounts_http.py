# services/user-service/tests/functional/test_local_accounts_http.py

"""Проверяет закрытый lookup/bootstrap контракт без раскрытия пароля или роли."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from pdrd_user_service.application.ports.repository import BootstrapAlreadyPerformed
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import Settings
from pdrd_user_service.domain.access import AccessTier
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.main import create_app

KEY = "test-only-service-key-32-characters"


class Catalog:
    """Подменяет открытые поля и предсказуемый конфликт bootstrap."""

    def __init__(self):
        """Создаёт активный локальный профиль."""
        self.user = UserAccount(
            uuid4(),
            UserKind.LOCAL,
            AccessTier.MEMBER,
            UserStatus.ACTIVE,
            "admin",
            datetime.now(UTC),
            login="admin",
        )
        self.conflict = False

    async def find_by_login(self, login):
        """Возвращает локальный источник только для известного имени."""
        return self.user if login == "admin" else None

    async def create(self, *, subject, username):
        """Применяет лишь subject и имя без пароля."""
        if self.conflict:
            raise BootstrapAlreadyPerformed
        return self.user

    async def is_ready(self):
        """Делает тестовое приложение готовым без сети."""
        return True


def make_client():
    """Собирает приватную границу без БД и сторонних соединений."""
    catalog = Catalog()

    async def close():
        """У подменённого приложения нет пулов."""

    container = ApplicationContainer(
        settings=Settings(
            _env_file=None,
            enabled=True,
            internal_key=KEY,
            database={"password": "test-only"},
        ),
        readiness=catalog,
        directory=catalog,
        shutdown_callback=close,
        local_superusers=catalog,
    )
    return TestClient(create_app(container)), catalog


def test_bootstrap_and_lookup_require_service_key_and_reject_secrets():
    """Ни браузерный запрос, ни password/role не проходят во внутренний каталог."""
    client, catalog = make_client()
    payload = {"subject": str(uuid4()), "username": "admin"}
    with client:
        assert (
            client.post("/internal/v1/users/local-superuser", json=payload).status_code
            == 401
        )
        headers = {"Authorization": f"Bearer {KEY}"}
        for extra in ("password", "role", "user_id"):
            assert (
                client.post(
                    "/internal/v1/users/local-superuser",
                    headers=headers,
                    json={**payload, extra: "forged"},
                ).status_code
                == 422
            )
        success = client.post(
            "/internal/v1/users/local-superuser", headers=headers, json=payload
        )
        assert success.status_code == 200 and success.json()["kind"] == "local"
        assert "password" not in success.text
        lookup = client.post(
            "/internal/v1/users/lookup-login", headers=headers, json={"login": "admin"}
        )
        assert lookup.json()["user_id"] == str(catalog.user.user_id)
        assert (
            client.post(
                "/internal/v1/users/lookup-login",
                headers=headers,
                json={"login": "missing"},
            ).status_code
            == 404
        )
        catalog.conflict = True
        assert (
            client.post(
                "/internal/v1/users/local-superuser", headers=headers, json=payload
            ).status_code
            == 409
        )
