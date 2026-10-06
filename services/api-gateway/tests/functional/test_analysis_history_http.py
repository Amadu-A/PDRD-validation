# services/api-gateway/tests/functional/test_analysis_history_http.py

"""HTTP-регрессия: история требует серверную сессию и игнорирует чужой UUID."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.core.settings import Settings
from pdrd_api_gateway.transport.http.identity_authorization import IdentityAuthorizer
from pdrd_api_gateway.transport.http.routers.analyses import router


def client(owner, history):
    """Проверяет cookie через настоящий IdentityAuthorizer, подменяя только сеть."""
    app = FastAPI()
    settings = IdentityProxySettings(
        enabled=True,
        authorization_enabled=True,
        public_origin="https://pdrd.example.test",
        auth_internal_key="a" * 32,
        user_service_internal_key="u" * 32,
        trusted_proxy_key="p" * 32,
        technical_assignment_access_key="t" * 32,
    )

    def introspect(request):
        """Чужие заголовки браузера не влияют на подтверждённый субъект."""
        if request.read().find(b"invalid") >= 0:
            return httpx.Response(401, json={})
        return httpx.Response(
            200, json={"user_id": str(owner), "permissions": ["analysis.run"]}
        )

    authorizer = IdentityAuthorizer(
        settings,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(introspect)
        ),
    )
    app.state.identity_authorizer = authorizer

    async def close():
        """Тест не создаёт сетевые подключения или рабочие ресурсы."""

    app.state.container = ApplicationContainer(
        settings=Settings(_env_file=None),
        check_readiness=None,
        shutdown_callback=close,
        list_analysis_history=history,
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        """Повторяет границу аутентификации главного приложения Gateway."""
        denial = await authorizer.authenticate_if_present(request)
        response = denial or await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
    return TestClient(app)


def test_anonymous_and_invalid_session_cannot_discover_jobs():
    """Гость и неверная cookie не запускают выборку истории."""
    history = SimpleNamespace(execute=AsyncMock(return_value={"items": []}))
    browser = client(uuid4(), history)
    assert browser.get("/api/v1/analyses/history").status_code == 401
    browser.cookies.set("pdrd_session", "invalid")
    assert browser.get("/api/v1/analyses/history").status_code == 401
    history.execute.assert_not_awaited()


def test_owner_is_derived_from_session_not_query_or_headers():
    """Пагинация ограничена, UUID из запроса не становится владельцем."""
    owner = uuid4()
    history = SimpleNamespace(
        execute=AsyncMock(return_value={"items": [], "has_more": False})
    )
    browser = client(owner, history)
    browser.cookies.set("pdrd_session", "valid")
    result = browser.get(
        "/api/v1/analyses/history",
        params={"owner_user_id": str(uuid4()), "limit": 5, "offset": 10},
        headers={"X-PDRD-Actor-Id": str(uuid4())},
    )
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    history.execute.assert_awaited_once_with(
        owner_user_id=owner, limit=5, offset=10, can_download_reviewed_pdf=False
    )
    assert browser.get("/api/v1/analyses/history?limit=21").status_code == 422
    assert browser.get("/api/v1/analyses/history?offset=-1").status_code == 422
