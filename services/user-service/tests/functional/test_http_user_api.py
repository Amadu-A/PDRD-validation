# services/user-service/tests/functional/test_http_user_api.py

"""Проверяет закрытые маршруты User Service без внешнего PostgreSQL."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_user_service.application.use_cases.users import (
    AdminRequired,
    PermissionSnapshot,
    UserNotFound,
)
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.access import AccessTier, Permission, Role
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.main import create_app

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTOR_ID = UUID("22222222-2222-4222-8222-222222222222")
AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
KEY = "test-internal-key-which-remains-private"


def profile() -> UserAccount:
    """Создаёт профиль для тестового внутреннего ответа."""
    return UserAccount(
        user_id=USER_ID,
        kind=UserKind.CORPORATE,
        tier=AccessTier.MEMBER,
        status=UserStatus.ACTIVE,
        display_name="Тестовый сотрудник",
        login="test.user",
        created_at=AT,
    )


class Probe:
    """Подменяет проверку PostgreSQL."""

    def __init__(self, ready: bool) -> None:
        """Запоминает готовность."""
        self.ready = ready
        self.calls = 0

    async def is_ready(self) -> bool:
        """Возвращает заданный результат и считает вызовы."""
        self.calls += 1
        return self.ready


class Directory:
    """Изолирует HTTP контракт от хранилища и проверки ролей."""

    def __init__(self) -> None:
        """Создаёт след вызовов."""
        self.calls: list[tuple[str, object]] = []
        self.fail_database = False

    async def get_user(self, user_id: UUID) -> UserAccount:
        """Возвращает профиль или безопасный not found."""
        self.calls.append(("get_user", user_id))
        if self.fail_database:
            raise RuntimeError("private database host and password")
        if user_id != USER_ID:
            raise UserNotFound(user_id)
        return profile()

    async def find_identity(
        self, provider_id: str, namespace: str, subject: str
    ) -> UserAccount | None:
        """Фиксирует точный составной ключ из JSON тела."""
        self.calls.append(("find_identity", (provider_id, namespace, subject)))
        if (provider_id, namespace, subject) == ("ad", "company", "i/mein?x=1"):
            return profile()
        return None

    async def provision(self, **values: object) -> UserAccount:
        """Фиксирует параметры закрытого создания профиля."""
        self.calls.append(("provision", values))
        return profile()

    async def permissions(self, user_id: UUID) -> PermissionSnapshot:
        """Возвращает права с версией и без пароля."""
        self.calls.append(("permissions", user_id))
        if user_id != USER_ID:
            raise UserNotFound(user_id)
        return PermissionSnapshot(
            user_id=user_id,
            authorization_version=3,
            tier=AccessTier.MEMBER,
            status=UserStatus.ACTIVE,
            roles=(Role.DESIGNER,),
            permissions=(Permission.ANALYSIS_RUN,),
        )

    async def assign(self, **values: object) -> None:
        """Проверяет, что HTTP не выдаёт роль по заявлению клиента."""
        self.calls.append(("assign", values))
        raise AdminRequired("В БД нет действующего администратора")

    async def revoke(self, **values: object) -> None:
        """Отклоняет отзыв при отсутствии роли администратора."""
        self.calls.append(("revoke", values))
        raise AdminRequired("В БД нет действующего администратора")


def make_app(
    *, enabled: bool = True, ready: bool = True
) -> tuple[object, Directory, Probe]:
    """Создаёт приложение с явными безопасными тестовыми зависимостями."""
    directory = Directory()
    probe = Probe(ready)
    settings = Settings(
        _env_file=None,
        enabled=enabled,
        internal_key=KEY if enabled else "",
        database=DatabaseSettings(password="private-test-password"),
    )

    async def close() -> None:
        """Завершает тестовый контейнер."""

    container = ApplicationContainer(
        settings=settings,
        readiness=probe,
        directory=directory if enabled else None,
        shutdown_callback=close,
    )
    return create_app(container), directory, probe


def test_disabled_service_has_no_business_routes_and_is_not_ready() -> None:
    """Опция запуска по умолчанию не публикует закрытый каталог."""
    app, directory, probe = make_app(enabled=False)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.get(f"/internal/v1/users/{USER_ID}").status_code == 404
    assert directory.calls == []
    assert probe.calls == 0


def test_readiness_checks_database_and_hides_schema() -> None:
    """Готовность зависит от БД, а Swagger/OpenAPI не публикуются."""
    app, _, probe = make_app(ready=False)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404
    assert probe.calls == 1


def test_internal_api_requires_exact_service_key() -> None:
    """Отсутствующий и неверный Bearer ключи не доходят до каталога."""
    app, directory, _ = make_app()
    with TestClient(app) as client:
        unauthorized = client.get(f"/internal/v1/users/{USER_ID}")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["cache-control"] == "no-store"
        assert (
            client.get(
                f"/internal/v1/users/{USER_ID}",
                headers={"Authorization": "Bearer invalid"},
            ).status_code
            == 401
        )
        response = client.get(
            f"/internal/v1/users/{USER_ID}",
            headers={"Authorization": f"Bearer {KEY}"},
        )
    assert response.status_code == 200
    assert response.json()["user_id"] == str(USER_ID)
    assert "password" not in response.text
    assert directory.calls == [("get_user", USER_ID)]


def test_identity_lookup_uses_json_body_not_url() -> None:
    """Специальные символы stable subject не попадают в URL журнала прокси."""
    app, directory, _ = make_app()
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/identities/resolve",
            headers={"Authorization": f"Bearer {KEY}"},
            json={
                "provider_id": "ad",
                "namespace": "company",
                "subject": "i/mein?x=1",
            },
        )
    assert response.status_code == 200
    assert directory.calls == [("find_identity", ("ad", "company", "i/mein?x=1"))]


def test_permissions_include_version_and_scope_warning() -> None:
    """Получатель знает, что операции не заменяют проверку объекта."""
    app, _, _ = make_app()
    with TestClient(app) as client:
        response = client.get(
            f"/internal/v1/users/{USER_ID}/permissions",
            headers={"Authorization": f"Bearer {KEY}"},
        )
    assert response.status_code == 200
    assert response.json()["authorization_version"] == 3
    assert response.json()["resource_scope_required"] is True
    assert response.json()["permissions"] == ["analysis.run"]
    assert response.headers["cache-control"] == "no-store"


def test_provision_rejects_password_and_client_role_fields() -> None:
    """Внутренний контракт не принимает пароль, хеш или готовую роль."""
    app, directory, _ = make_app()
    payload = {
        "provider_id": "ad",
        "namespace": "company",
        "subject": "user-object-guid",
        "kind": "corporate",
        "display_name": "Сотрудник",
        "login": "test.user",
    }
    with TestClient(app) as client:
        valid = client.post(
            "/internal/v1/users",
            headers={"Authorization": f"Bearer {KEY}"},
            json=payload,
        )
        password = client.post(
            "/internal/v1/users",
            headers={"Authorization": f"Bearer {KEY}"},
            json={**payload, "password": "never-store-me"},
        )
        role = client.post(
            "/internal/v1/users",
            headers={"Authorization": f"Bearer {KEY}"},
            json={**payload, "role": "platform_admin"},
        )
    assert valid.status_code == 200
    assert password.status_code == 422
    assert role.status_code == 422
    assert [name for name, _ in directory.calls] == ["provision"]


def test_role_mutation_requires_actor_uuid_and_database_admin() -> None:
    """Ключ сервиса без действующей роли человека не даёт назначать роли."""
    app, directory, _ = make_app()
    payload = {"role": "designer", "scope": {"kind": "own"}}
    with TestClient(app) as client:
        missing_actor = client.post(
            f"/internal/v1/users/{USER_ID}/roles",
            headers={"Authorization": f"Bearer {KEY}"},
            json=payload,
        )
        denied = client.post(
            f"/internal/v1/users/{USER_ID}/roles",
            headers={
                "Authorization": f"Bearer {KEY}",
                "X-PDRD-Actor-Id": str(ACTOR_ID),
                "X-PDRD-Role": "platform_admin",
            },
            json=payload,
        )
        role_source_rejected = client.post(
            f"/internal/v1/users/{USER_ID}/roles",
            headers={
                "Authorization": f"Bearer {KEY}",
                "X-PDRD-Actor-Id": str(ACTOR_ID),
            },
            json={**payload, "source": "ad_group"},
        )
        naive_expiry_rejected = client.post(
            f"/internal/v1/users/{USER_ID}/roles",
            headers={
                "Authorization": f"Bearer {KEY}",
                "X-PDRD-Actor-Id": str(ACTOR_ID),
            },
            json={**payload, "expires_at": "2026-10-01T12:00:00"},
        )
    assert missing_actor.status_code == 422
    assert denied.status_code == 403
    assert role_source_rejected.status_code == 422
    assert naive_expiry_rejected.status_code == 422
    assert [name for name, _ in directory.calls] == ["assign"]


def test_unhandled_database_error_does_not_leak_details() -> None:
    """Сбой инфраструктуры остаётся 500 без выдачи частных данных."""
    app, directory, _ = make_app()
    directory.fail_database = True
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/internal/v1/users/{USER_ID}",
            headers={"Authorization": f"Bearer {KEY}"},
        )
    assert response.status_code == 500
    assert "private database host" not in response.text
