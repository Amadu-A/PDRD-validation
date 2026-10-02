# services/api-gateway/tests/integration/test_identity_proxy_endpoints.py

"""Проверяет браузерный прокси Auth/Admin через настоящее FastAPI приложение."""

import httpx
from fastapi.testclient import TestClient
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.core.settings import DatabaseSettings, Settings
from pdrd_api_gateway.infrastructure.identity_proxy import IdentityProxy
from pdrd_api_gateway.main import create_app
from pydantic import SecretStr


def make_app(upstream: httpx.MockTransport, *, enabled: bool = True) -> object:
    """Собирает Gateway с подменённым только закрытым HTTP-клиентом."""
    settings = Settings(
        _env_file=None,
        database=DatabaseSettings(password="test-only-database-password"),
        identity_proxy=IdentityProxySettings(
            enabled=enabled,
            authorization_enabled=enabled,
            auth_internal_key=SecretStr("a" * 32),
            user_service_internal_key=SecretStr("u" * 32),
            technical_assignment_access_key=SecretStr("t" * 32),
            trusted_proxy_key=SecretStr("p" * 32),
            public_origin="https://pdrd.example.test",
        ),
    )

    async def close() -> None:
        """Завершает тестовый контейнер."""

    container = ApplicationContainer(
        settings=settings,
        check_readiness=None,
        shutdown_callback=close,
        identity_proxy=(
            IdentityProxy(
                settings.identity_proxy,
                client_factory=lambda: httpx.AsyncClient(
                    transport=upstream, follow_redirects=False, trust_env=False
                ),
            )
            if enabled
            else None
        ),
    )
    return create_app(container)


def test_disabled_proxy_does_not_publish_identity_routes() -> None:
    """Маршруты не появляются до включения контура конфигурацией."""
    app = make_app(httpx.MockTransport(lambda _: httpx.Response(200)), enabled=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/session").status_code == 404
        assert client.get("/api/v1/users/me").status_code == 404
        assert client.get("/api/v1/admin/users").status_code == 404


def test_auth_profile_and_admin_routes_preserve_cookie_and_strip_internal_headers() -> (
    None
):
    """Gateway оставляет браузеру Set-Cookie и не выдаёт сервису поддельный Actor ID."""
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        """Возвращает два cookie на login и фиксирует адрес маршрута."""
        seen.append(request)
        if request.url.path.endswith("/login"):
            return httpx.Response(
                200,
                json={"authenticated": True},
                headers=[
                    ("Set-Cookie", "pdrd_session=opaque; HttpOnly; Secure; Path=/"),
                    ("Set-Cookie", "pdrd_csrf=nonce; Secure; Path=/"),
                ],
            )
        return httpx.Response(200, json={"ok": True})

    app = make_app(httpx.MockTransport(upstream))
    with TestClient(
        app, base_url="https://testserver", client=("198.51.100.9", 50000)
    ) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "tester", "password": "private"},
            headers={
                "Origin": "https://pdrd.example.test",
                "X-CSRF-Token": "nonce",
                "Authorization": "Bearer forged",
                "X-PDRD-Actor-Id": "forged-id",
                "X-PDRD-Client-IP": "203.0.113.99",
                "X-Real-IP": "192.0.2.4",
                "X-Internal-Key": "forged-key",
                "X-Forwarded-Host": "evil.example",
            },
        )
        profile = client.get(
            "/api/v1/users/me",
            headers={"X-PDRD-Client-IP": "203.0.113.99"},
        )
        profile_update = client.patch("/api/v1/users/me", json={"display_name": "x"})
        admin = client.get("/api/v1/admin/users?limit=5&offset=10")
    assert login.status_code == 200
    assert len(login.headers.get_list("set-cookie")) == 2
    assert login.headers["cache-control"] == "no-store"
    assert profile.status_code == 200
    assert profile_update.status_code == 405
    assert admin.status_code == 200
    assert [request.url.path for request in seen] == [
        "/api/v1/auth/login",
        "/api/v1/users/me",
        "/api/v1/admin/users",
    ]
    assert seen[-1].url.query == b"limit=5&offset=10"
    assert seen[0].headers["origin"] == "https://pdrd.example.test"
    assert seen[0].headers["x-csrf-token"] == "nonce"
    assert seen[0].headers["x-pdrd-client-ip"] == "198.51.100.9"
    assert seen[1].headers["x-pdrd-client-ip"] == "198.51.100.9"
    for request in seen:
        for forbidden in (
            "authorization",
            "x-pdrd-actor-id",
            "x-internal-key",
            "x-forwarded-host",
        ):
            assert forbidden not in request.headers
    assert seen[1].headers["cookie"].startswith("pdrd_session=opaque")


def test_only_server_key_allows_nginx_client_ip() -> None:
    """Поддельный X-Real-IP без ключа не обходит лимит запросов Auth Service."""
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        """Запоминает проверенный Gateway адрес, переданный Auth Service."""
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    app = make_app(httpx.MockTransport(upstream))
    with TestClient(
        app, base_url="https://testserver", client=("172.30.0.2", 50000)
    ) as client:
        client.get(
            "/api/v1/auth/session",
            headers={"X-Real-IP": "192.0.2.4", "X-PDRD-Proxy-Key": "forged"},
        )
        client.get(
            "/api/v1/auth/session",
            headers={
                "X-Real-IP": "192.0.2.4",
                "X-PDRD-Proxy-Key": "p" * 32,
            },
        )
    assert seen[0].headers["x-pdrd-client-ip"] == "172.30.0.2"
    assert seen[1].headers["x-pdrd-client-ip"] == "192.0.2.4"
    assert "x-pdrd-proxy-key" not in seen[0].headers
    assert "x-pdrd-proxy-key" not in seen[1].headers


def test_admin_membership_put_reaches_admin_service() -> None:
    """Изменение членства использует PUT и проходит оба слоя Gateway proxy."""
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        """Фиксирует адрес, метод и тело перед передачей в Admin Service."""
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    app = make_app(httpx.MockTransport(upstream))
    path = (
        "/api/v1/admin/users/11111111-1111-4111-8111-111111111111"
        "/memberships/22222222-2222-4222-8222-222222222222"
        "/33333333-3333-4333-8333-333333333333"
    )
    with TestClient(app, base_url="https://testserver") as client:
        response = client.put(
            path,
            json={"expected_version": 2},
            headers={"Origin": "https://pdrd.example.test"},
        )
    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0].method == "PUT"
    assert seen[0].url.path == path
    assert seen[0].headers["origin"] == "https://pdrd.example.test"
    assert seen[0].content == b'{"expected_version":2}'


def test_unavailable_upstream_returns_generic_503_and_large_body_is_rejected() -> None:
    """Внутренний URL не раскрывается при сбое, большие тела не идут в сеть."""
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        """Моделирует недоступный закрытый сервис."""
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("private-auth-host:8000", request=request)

    app = make_app(httpx.MockTransport(upstream))
    with TestClient(app) as client:
        unavailable = client.get("/api/v1/auth/session")
        too_large = client.post(
            "/api/v1/auth/register",
            content=b"x" * (64 * 1024 + 1),
        )
    assert unavailable.status_code == 503
    assert unavailable.headers["cache-control"] == "no-store"
    assert "private-auth-host" not in unavailable.text
    assert too_large.status_code == 413
    assert calls == 1
