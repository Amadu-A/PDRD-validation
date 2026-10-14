# services/user-service/tests/functional/test_http_equipment_sources_access.py

"""Закрытое назначение управления источниками оборудования: служебный ключ, актёр, строгая команда и CAS."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.equipment_sources_access import (
    EquipmentSourcesAccessChange,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import Settings
from pdrd_user_service.domain.access import AccessTier
from pdrd_user_service.domain.equipment_sources_access import (
    EquipmentSourcesAccessState,
)
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.main import create_app

KEY = "internal-equipment-sources-access-test-key-at-least-32"
ACTOR, TARGET = uuid4(), uuid4()


class Management:
    """Запоминает доверенный контекст и имитирует известные отказы."""

    def __init__(self):
        """Создаёт пустой след и необязательный отказ."""
        self.calls = []
        self.error = None

    async def change(self, **kwargs):
        """Возвращает обновлённый профиль без полей аутентификации."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        user = UserAccount(
            TARGET,
            UserKind.CORPORATE,
            AccessTier.MEMBER,
            UserStatus.ACTIVE,
            "Проектировщик",
            datetime.now(UTC),
            login="designer",
        )
        return EquipmentSourcesAccessChange(
            replace(
                user, authorization_version=2, equipment_sources_access_enabled=True
            ),
            EquipmentSourcesAccessState(True, False, True),
        )


def make_client():
    """Собирает закрытое приложение без БД и сети."""
    management = Management()

    async def close():
        """Освобождает пустой контейнер."""

    container = ApplicationContainer(
        settings=Settings(
            _env_file=None,
            enabled=True,
            internal_key=KEY,
            database={"password": "test-only-password"},
        ),
        readiness=None,
        directory=None,
        shutdown_callback=close,
        equipment_sources_access=management,
    )
    return TestClient(create_app(container)), management


def test_internal_equipment_sources_access_requires_key_actor_and_strict_cas():
    """Браузер не может обратиться без ключа или передать себе дополнительные права."""
    client, management = make_client()
    path = f"/internal/v1/users/{TARGET}/equipment-sources-access"
    headers = {"Authorization": f"Bearer {KEY}", "X-PDRD-Actor-Id": str(ACTOR)}
    payload = {"enabled": True, "authorization_version": 1}
    with client:
        assert client.patch(path, json=payload).status_code == 401
        assert (
            client.patch(
                path, headers={"Authorization": f"Bearer {KEY}"}, json=payload
            ).status_code
            == 422
        )
        for invalid in (
            {**payload, "role": "platform_admin"},
            {**payload, "enabled": "true"},
            {**payload, "authorization_version": True},
            {**payload, "authorization_version": 0},
        ):
            assert client.patch(path, headers=headers, json=invalid).status_code == 422
        response = client.patch(path, headers=headers, json=payload)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["equipment_sources_access"]
    assert response.json()["user"]["authorization_version"] == 2
    assert "equipment_sources_access_enabled" not in response.json()["user"]
    assert management.calls == [
        {
            "actor_user_id": ACTOR,
            "target_user_id": TARGET,
            "enabled": True,
            "authorization_version": 1,
        }
    ]


@pytest.mark.parametrize(
    "error, status",
    [
        (AdminRequired(), 403),
        (UserNotFound(), 404),
        (AuthorizationConflict(), 409),
        (ValueError("Автоматический доступ нельзя снять"), 422),
    ],
)
def test_internal_equipment_sources_access_translates_expected_errors(error, status):
    """Известные отказы не превращаются в 500 или выдачу внутреннего состояния."""
    client, management = make_client()
    management.error = error
    with client:
        response = client.patch(
            f"/internal/v1/users/{TARGET}/equipment-sources-access",
            headers={"Authorization": f"Bearer {KEY}", "X-PDRD-Actor-Id": str(ACTOR)},
            json={"enabled": False, "authorization_version": 1},
        )
    assert response.status_code == status
