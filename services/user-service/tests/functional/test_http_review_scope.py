# services/user-service/tests/functional/test_http_review_scope.py

"""Проверяет закрытый boolean контракт области Review для Gateway."""

from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.main import create_app

KEY = "test-internal-key-which-remains-private"
ACTOR = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
OWNER = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")


class Ready:
    """Минимальная проверка готовности без БД."""

    async def is_ready(self) -> bool:
        """Возвращает тестовую готовность."""
        return True


class Scope:
    """Фиксирует точные UUID из тела запроса."""

    def __init__(self) -> None:
        """Сохраняет отсутствие вызовов до проверки ключа."""
        self.calls: list[tuple[UUID, UUID]] = []

    async def can_read(self, *, actor_user_id: UUID, owner_user_id: UUID) -> bool:
        """Возвращает boolean без персональных деталей."""
        self.calls.append((actor_user_id, owner_user_id))
        return actor_user_id == ACTOR and owner_user_id == OWNER


def test_review_scope_requires_key_and_exact_uuid_body() -> None:
    """Открытый клиент не может проверить чужую область без сервисного ключа."""
    scope = Scope()
    settings = Settings(
        _env_file=None,
        enabled=True,
        internal_key=KEY,
        database=DatabaseSettings(password="private-test-password"),
    )

    async def close() -> None:
        """Закрывает пустой контейнер."""

    app = create_app(
        ApplicationContainer(
            settings=settings,
            readiness=Ready(),
            directory=None,
            shutdown_callback=close,
            review_scope_access=scope,
        )
    )
    path = "/internal/v1/access/review-scope"
    payload = {"actor_user_id": str(ACTOR), "owner_user_id": str(OWNER)}
    with TestClient(app) as client:
        assert client.post(path, json=payload).status_code == 401
        headers = {"Authorization": f"Bearer {KEY}"}
        assert (
            client.post(
                path, json={**payload, "role": "platform_admin"}, headers=headers
            ).status_code
            == 422
        )
        allowed = client.post(path, json=payload, headers=headers)
        denied = client.post(
            path,
            json={**payload, "owner_user_id": str(ACTOR)},
            headers=headers,
        )
    assert allowed.status_code == 200 and allowed.json() == {"allowed": True}
    assert denied.status_code == 200 and denied.json() == {"allowed": False}
    assert scope.calls == [(ACTOR, OWNER), (ACTOR, ACTOR)]
