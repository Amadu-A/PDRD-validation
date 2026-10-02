# services/api-gateway/tests/unit/test_identity_proxy.py

"""Проверяет адреса сервиса, фильтрацию заголовков и изоляцию cookie."""

import httpx
import pytest
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.infrastructure.identity_proxy import IdentityProxy
from pydantic import SecretStr, ValidationError


def enabled_settings() -> IdentityProxySettings:
    """Готовит замкнутый тестовый контур с разными служебными ключами."""
    return IdentityProxySettings(
        enabled=True,
        authorization_enabled=True,
        auth_internal_key=SecretStr("a" * 32),
        user_service_internal_key=SecretStr("u" * 32),
        technical_assignment_access_key=SecretStr("t" * 32),
        trusted_proxy_key=SecretStr("p" * 32),
        public_origin="https://pdrd.example.test",
    )


def test_identity_proxy_disabled_by_default_and_rejects_unsafe_base_url() -> None:
    """Новый контур требует явного включения и адреса без чужого пути/секрета."""
    assert not IdentityProxySettings().enabled
    for url in (
        "http://auth-service:8000/internal",
        "http://user:password@auth-service:8000",
        "http://auth-service:8000?redirect=evil",
        "file:///etc/passwd",
        "http://auth-service:99999",
    ):
        with pytest.raises(ValidationError):
            IdentityProxySettings(auth_base_url=url)


@pytest.mark.asyncio
async def test_identity_proxy_strips_client_authority_headers_and_cookie_jar() -> None:
    """Второй посетитель не наследует cookie первого через HTTP-клиент."""
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        """Запоминает полученные сервисом заголовки."""
        seen.append(request)
        return httpx.Response(
            200,
            json={"ok": True},
            headers=[
                ("Set-Cookie", "pdrd_session=first; HttpOnly; Secure; Path=/"),
                ("Set-Cookie", "pdrd_csrf=one; Secure; Path=/"),
            ],
        )

    transport = httpx.MockTransport(upstream)
    proxy = IdentityProxy(
        enabled_settings(),
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    )
    first = await proxy.forward(
        service="auth",
        method="POST",
        path="/api/v1/auth/login",
        query="",
        headers={
            "cookie": "browser=first",
            "origin": "https://pdrd.example.test",
            "x-csrf-token": "csrf-value",
            "authorization": "Bearer client-forgery",
            "x-pdrd-actor-id": "forged-admin",
            "x-pdrd-client-ip": "203.0.113.99",
            "x-pdrd-internal-key": "forged-secret",
            "x-forwarded-host": "evil.example",
        },
        body=b'{"login":"test"}',
        client_ip="192.0.2.4",
    )
    await proxy.forward(
        service="auth",
        method="GET",
        path="/api/v1/auth/session",
        query="",
        headers={},
        body=b"",
        client_ip="192.0.2.5",
    )
    assert len(first.set_cookies) == 2
    assert seen[0].headers["cookie"] == "browser=first"
    assert seen[0].headers["origin"] == "https://pdrd.example.test"
    assert seen[0].headers["x-csrf-token"] == "csrf-value"
    assert seen[0].headers["x-pdrd-client-ip"] == "192.0.2.4"
    assert seen[1].headers["x-pdrd-client-ip"] == "192.0.2.5"
    for forbidden in (
        "authorization",
        "x-pdrd-actor-id",
        "x-pdrd-internal-key",
        "x-forwarded-host",
    ):
        assert forbidden not in seen[0].headers
    assert "cookie" not in seen[1].headers


@pytest.mark.asyncio
async def test_identity_proxy_rejects_path_escape_before_upstream_call() -> None:
    """Точечный обход не может превратиться во внутренний маршрут сервиса."""
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        """Считает обращения к сети."""
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(200)

    proxy = IdentityProxy(
        enabled_settings(),
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(upstream), trust_env=False
        ),
    )
    for path in (
        "/api/v1/auth/../internal/v1/users",
        "/internal/v1/users",
        "/api/v1/admin/%2e%2e/auth/session",
    ):
        with pytest.raises(ValueError, match="маршрут"):
            await proxy.forward(
                service="auth" if "auth" in path else "admin",
                method="GET",
                path=path,
                query="",
                headers={},
                body=b"",
                client_ip="192.0.2.4",
            )
    assert calls == 0
