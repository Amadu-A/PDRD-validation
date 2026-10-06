# services/user-service/tests/functional/test_http_external_accounts.py

"""Проверяет закрытый HTTP-контракт email-профиля без SMTP и PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_user_service.application.use_cases.external_accounts import (
    ExternalAccountConflict,
)
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.access import AccessTier
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.main import create_app

KEY = "test-internal-key-which-remains-private"
USER_ID = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
SUBJECT = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")


class Ready:
    """Предоставляет контейнеру интерфейс readiness."""

    async def is_ready(self) -> bool:
        """Возвращает готовность тестового сервиса."""
        return True


class Accounts:
    """Фиксирует только данные, разрешённые внутренним контрактом."""

    def __init__(self) -> None:
        """Создаёт ожидающий профиль и пустой след вызовов."""
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.pending = UserAccount(
            user_id=USER_ID,
            kind=UserKind.EXTERNAL,
            tier=AccessTier.REGISTERED_FREE,
            status=UserStatus.PENDING_VERIFICATION,
            display_name="Клиент",
            email="client@example.test",
            created_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        )

    async def register(self, **values: object) -> UserAccount:
        """Возвращает состояние до проверки адреса."""
        self.calls.append(("register", values))
        return self.pending

    async def verify_email(self, **values: object) -> UserAccount:
        """Отклоняет чужой subject и фиксирует активацию правильного."""
        self.calls.append(("verify", values))
        if values["subject"] != SUBJECT:
            raise ExternalAccountConflict("Чужой subject")
        return replace(
            self.pending,
            status=UserStatus.ACTIVE,
            authorization_version=2,
        )


def client_and_accounts() -> tuple[TestClient, Accounts]:
    """Собирает приложение без настоящих внешних подключений."""
    accounts = Accounts()
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
        external_accounts=accounts,
    )
    return TestClient(create_app(container)), accounts


def test_registration_requires_service_key_and_rejects_credentials() -> None:
    """Пароль, роль и токен не проходят в User Service даже с ключом."""
    client, accounts = client_and_accounts()
    payload = {
        "subject": str(SUBJECT),
        "display_name": "Клиент",
        "email": "client@example.test",
    }
    with client:
        missing_key = client.post("/internal/v1/users/external", json=payload)
        valid = client.post(
            "/internal/v1/users/external",
            json=payload,
            headers={"Authorization": f"Bearer {KEY}"},
        )
        for field in ("password", "password_hash", "role", "verification_token"):
            rejected = client.post(
                "/internal/v1/users/external",
                json={**payload, field: "secret"},
                headers={"Authorization": f"Bearer {KEY}"},
            )
            assert rejected.status_code == 422
    assert missing_key.status_code == 401
    assert valid.status_code == 200
    assert valid.json()["status"] == "pending_verification"
    assert valid.json()["tier"] == "registered_free"
    assert "password" not in valid.text
    assert valid.headers["cache-control"] == "no-store"
    assert accounts.calls == [
        (
            "register",
            {**payload, "subject": SUBJECT},
        )
    ]


def test_verification_checks_stable_subject_and_hides_private_fields() -> None:
    """После email-проверки клиент получает только активный профиль."""
    client, accounts = client_and_accounts()
    with client:
        unauthorized = client.post(
            f"/internal/v1/users/{USER_ID}/verify-email",
            json={"subject": str(SUBJECT)},
        )
        mismatch = client.post(
            f"/internal/v1/users/{USER_ID}/verify-email",
            json={"subject": str(USER_ID)},
            headers={"Authorization": f"Bearer {KEY}"},
        )
        active = client.post(
            f"/internal/v1/users/{USER_ID}/verify-email",
            json={"subject": str(SUBJECT)},
            headers={"Authorization": f"Bearer {KEY}"},
        )
    assert unauthorized.status_code == 401
    assert mismatch.status_code == 409
    assert active.status_code == 200
    assert active.json()["status"] == "active"
    assert active.json()["authorization_version"] == 2
    assert "verification_token" not in active.text
    assert accounts.calls == [
        ("verify", {"user_id": USER_ID, "subject": USER_ID}),
        ("verify", {"user_id": USER_ID, "subject": SUBJECT}),
    ]
