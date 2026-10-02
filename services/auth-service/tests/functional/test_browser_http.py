"""Проверяет публичный HTTP-контракт без подключения к AD, SMTP или рабочей БД."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pdrd_auth_service.application.ports.profiles import (
    PermissionSnapshot,
    ProfileSnapshot,
)
from pdrd_auth_service.application.use_cases.current_user import CurrentUser
from pdrd_auth_service.application.use_cases.sessions import (
    IssuedSession,
    SessionView,
    UserStateUnavailable,
)
from pdrd_auth_service.core.settings import EmailSettings, HttpSettings, SessionSettings
from pdrd_auth_service.main import create_app
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

TOKEN = "a" * 43
ORIGIN = "https://pdrd.example"


class FakeLimiter:
    """Сохраняет количество вызовов лимитера на входе HTTP."""

    def __init__(self) -> None:
        """Создаёт пустой журнал попыток."""
        self.attempts: list[str] = []

    async def allow(self, **kwargs: object) -> bool:
        """Разрешает короткий тест и фиксирует namespace."""
        self.attempts.append(str(kwargs["namespace"]))
        return True


class FakeExternal:
    """Фиксирует регистрацию и погашение кода без настоящей почты."""

    def __init__(self) -> None:
        """Инициализирует состояние отправки и подтверждения."""
        self.registered = False
        self.verified = False

    async def register(self, **_: str) -> None:
        """Отмечает достижение use case после проверки Origin."""
        self.registered = True

    async def verify_email(self, _: str) -> None:
        """Отмечает достижение use case подтверждения."""
        self.verified = True


class FakeSessions:
    """Запоминает отзыв сессий в HTTP-тесте."""

    def __init__(self, session: SessionView) -> None:
        """Хранит одну собственную сессию и состояние её отзыва."""
        self.session = session
        self.revoked = False
        self.revoked_all = False

    async def revoke_current(self, _: str) -> None:
        """Имитирует завершение текущего устройства."""
        self.revoked = True

    async def revoke_all(self, user_id: object) -> int:
        """Отзывает сессии только подтверждённого пользователя."""
        assert user_id == self.session.user_id
        self.revoked_all = True
        return 1

    async def revoke_owned(self, user_id: object, session_id: object) -> bool:
        """Отклоняет чужой идентификатор сессии."""
        if user_id != self.session.user_id or session_id != self.session.session_id:
            return False
        self.revoked = True
        return True

    async def list_user(self, user_id: object) -> tuple[SessionView, ...]:
        """Отдаёт метаданные одного собственного устройства."""
        assert user_id == self.session.user_id
        return (self.session,)


def fake_runtime() -> SimpleNamespace:
    """Даёт маршрутам полный проверенный снимок пользователя и конфигурации."""
    now = datetime.now(UTC)
    user_id = uuid4()
    session = SessionView(
        session_id=uuid4(),
        user_id=user_id,
        authorization_version=1,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=2),
        absolute_expires_at=now + timedelta(hours=8),
    )
    current_user = CurrentUser(
        session=session,
        profile=ProfileSnapshot(
            user_id=user_id,
            kind="external",
            tier="registered_free",
            status="active",
            display_name="Тестовый пользователь",
            login=None,
            email="test@example.org",
            authorization_version=1,
        ),
        permissions=PermissionSnapshot(
            user_id=user_id,
            authorization_version=1,
            status="active",
            tier="registered_free",
            roles=(),
            permissions=("analysis.run",),
        ),
    )

    async def resolve(token: str) -> CurrentUser:
        """Отклоняет неизвестную cookie."""
        if token != TOKEN:
            raise PermissionError
        return current_user

    async def issue(**_: str) -> IssuedSession:
        """Имитирует успешную проверку пароля и выпуск сессии."""
        return IssuedSession(token=TOKEN, session=session)

    return SimpleNamespace(
        settings=SimpleNamespace(
            http=HttpSettings(
                enabled=True,
                public_origin=ORIGIN,
                internal_key=SecretStr("i" * 32),
                csrf_key=SecretStr("c" * 32),
            ),
            email=EmailSettings(enabled=True),
            sessions=SessionSettings(),
        ),
        current=SimpleNamespace(execute=resolve),
        login=SimpleNamespace(execute=issue),
        external=FakeExternal(),
        sessions=FakeSessions(session),
        limiter=FakeLimiter(),
    )


@pytest.mark.asyncio
async def test_guest_login_session_logout_and_csrf() -> None:
    """Cookie выдаётся только после входа, а отзыв требует CSRF-токен."""
    runtime = fake_runtime()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime)), base_url=ORIGIN
    ) as client:
        guest = await client.get("/api/v1/auth/session")
        assert guest.json()["authenticated"] is False

        rejected = await client.post(
            "/api/v1/auth/login", json={"login": "x", "password": "x"}
        )
        assert rejected.status_code == 403

        logged_in = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"login": "test@example.org", "password": "example-secret-12"},
        )
        assert logged_in.status_code == 200
        assert "httponly" in logged_in.headers["set-cookie"].lower()
        assert "secure" in logged_in.headers["set-cookie"].lower()

        active = await client.get("/api/v1/auth/session")
        body = active.json()
        assert body["authenticated"] is True
        assert body["user"]["permissions"] == ["analysis.run"]
        assert TOKEN not in str(body)
        assert (
            await client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
        ).status_code == 403
        logout = await client.post(
            "/api/v1/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": body["csrf_token"]},
        )
        assert logout.status_code == 200
        assert runtime.sessions.revoked is True


@pytest.mark.asyncio
async def test_logout_clears_expired_cookie_without_csrf() -> None:
    """Истёкшая серверная сессия не оставляет браузер без гостевого анализа."""
    runtime = fake_runtime()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime)), base_url=ORIGIN
    ) as client:
        client.cookies.set("pdrd_session", "expired-token")
        response = await client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
        assert response.status_code == 200
        assert "max-age=0" in response.headers["set-cookie"].lower()
        assert runtime.sessions.revoked is False


@pytest.mark.asyncio
async def test_registration_requires_origin_and_internal_introspection_requires_key() -> (
    None
):
    """Публичная регистрация и служебное чтение сессии имеют разные границы."""
    runtime = fake_runtime()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime)), base_url=ORIGIN
    ) as client:
        command = {
            "display_name": "Тестовый пользователь",
            "email": "test@example.org",
            "password": "example-secret-12",
        }
        assert (
            await client.post("/api/v1/auth/register", json=command)
        ).status_code == 403
        assert runtime.external.registered is False
        accepted = await client.post(
            "/api/v1/auth/register", json=command, headers={"Origin": ORIGIN}
        )
        assert accepted.status_code == 202
        assert runtime.external.registered is True
        assert (
            await client.post("/internal/v1/auth/introspect", json={"token": TOKEN})
        ).status_code == 403
        introspected = await client.post(
            "/internal/v1/auth/introspect",
            json={"token": TOKEN},
            headers={"Authorization": f"Bearer {'i' * 32}"},
        )
        assert introspected.status_code == 200
        assert introspected.json()["permissions"] == ["analysis.run"]
        assert "csrf_token" in introspected.json()


@pytest.mark.asyncio
async def test_email_confirmation_and_session_management_http_contract() -> None:
    """Подтверждение, просмотр устройств и отзыв требуют корректную границу."""
    runtime = fake_runtime()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime)), base_url=ORIGIN
    ) as client:
        verify = await client.post(
            "/api/v1/auth/verify-email",
            headers={"Origin": ORIGIN},
            json={"token": "v" * 43},
        )
        assert verify.status_code == 200
        assert runtime.external.verified is True

        client.cookies.set("pdrd_session", TOKEN)
        active = await client.get("/api/v1/auth/session")
        csrf = active.json()["csrf_token"]
        devices = await client.get("/api/v1/auth/sessions")
        assert devices.status_code == 200
        assert devices.json()["sessions"][0]["session_id"] == str(
            runtime.sessions.session.session_id
        )
        assert TOKEN not in devices.text

        own_url = f"/api/v1/auth/sessions/{runtime.sessions.session.session_id}"
        assert (
            await client.delete(own_url, headers={"Origin": ORIGIN})
        ).status_code == 403
        foreign = await client.delete(
            f"/api/v1/auth/sessions/{uuid4()}",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
        assert foreign.status_code == 404
        assert runtime.sessions.revoked is False
        own = await client.delete(
            own_url, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert own.status_code == 204
        assert runtime.sessions.revoked is True
        assert "max-age=0" in own.headers["set-cookie"].lower()

        client.cookies.set("pdrd_session", TOKEN)
        logout_all = await client.post(
            "/api/v1/auth/logout-all",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
        assert logout_all.status_code == 200
        assert runtime.sessions.revoked_all is True
        assert "max-age=0" in logout_all.headers["set-cookie"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["user-service", "database"])
async def test_dependency_failure_rejects_session_without_leaking_details(
    failure: str,
) -> None:
    """Сбой каталога или БД не превращается в 500 и не раскрывает детали."""
    runtime = fake_runtime()

    async def unavailable(_: str) -> CurrentUser:
        """Имитирует отказ обязательной зависимости во время проверки cookie."""
        if failure == "user-service":
            raise UserStateUnavailable("internal-user-service-host")
        raise OperationalError("SELECT secret", {}, RuntimeError("database-secret"))

    runtime.current.execute = unavailable
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime)), base_url=ORIGIN
    ) as client:
        client.cookies.set("pdrd_session", TOKEN)
        response = await client.get("/api/v1/auth/session")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Сервис временно недоступен"}
    assert "secret" not in response.text
