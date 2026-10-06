# services/api-gateway/tests/functional/test_experience_authors_http.py

"""HTTP контур руководителя: авторы из User Service, фильтр и закрытые административные команды."""

from uuid import UUID

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.manage_experience import ManageExperience
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.core.settings import DatabaseSettings, ReviewSettings, Settings
from pdrd_api_gateway.infrastructure.experience import (
    ControlledExperienceAccess,
    ControlledExperienceContext,
    HttpExperienceService,
)
from pdrd_api_gateway.infrastructure.experience_authors import HttpExperienceAuthors
from pdrd_api_gateway.transport.http.identity_authorization import (
    IdentityAuthorizer,
    enforce_identity_authorization,
)
from pdrd_api_gateway.transport.http.routers.experience import router

ACTOR, AUTHOR, EXAMPLE, JOB = (UUID(int=value) for value in range(1, 5))


class Reviews:
    """Существование завершённого задания не связано с административными мутациями каталога."""

    async def require(self, context):
        """Проверяет серверное задание только при чтении конкретного примера."""
        assert context.job_id == JOB


def test_head_can_read_authors_filter_and_history_but_not_mutate_catalog():
    """Браузерные Actor заголовки игнорируются; права проверяет настоящий middleware."""
    calls = []
    profile_calls = []
    example = {
        "id": str(EXAMPLE),
        "job_id": str(JOB),
        "source": {"created_by": f"user:{AUTHOR}"},
    }

    def experience(request):
        """Фиксирует только приватные маршруты, разрешённые прикладным шлюзом."""
        calls.append(request)
        assert request.headers["X-Review-Actor"] == f"user:{ACTOR}"
        if request.url.path.endswith("/authors"):
            return httpx.Response(
                200, json={"items": [{"id": f"user:{AUTHOR}"}], "total": 1}
            )
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"events": []})
        if request.url.path.endswith(str(EXAMPLE)):
            return httpx.Response(200, json=example)
        return httpx.Response(200, json={"items": [example], "total": 1})

    def profiles(request):
        """Проекция профиля возвращается только с подтверждённым actor Gateway."""
        profile_calls.append(request)
        assert request.headers["X-PDRD-Actor-Id"] == str(ACTOR)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": str(AUTHOR),
                        "login": "i.mein",
                        "display_name": "Иван Мейн",
                        "roles": ["department_head"],
                    }
                ]
            },
        )

    def introspect(request):
        """Тестовая сессия даёт право читать каталог, но не администрировать его."""
        return httpx.Response(
            200,
            json={"user_id": str(ACTOR), "permissions": ["experience.catalog.read"]},
        )

    identity = IdentityProxySettings(
        enabled=True,
        authorization_enabled=True,
        technical_assignment_access_key="t" * 32,
        trusted_proxy_key="p" * 32,
        public_origin="https://pdrd.example.test",
        auth_internal_key="a" * 32,
        user_service_internal_key="u" * 32,
    )
    review = ReviewSettings(
        enabled=True,
        controlled_access=True,
        actor="engineer:server",
        ui_key="f" * 32,
        internal_key="r" * 32,
    )
    use_case = ManageExperience(
        ControlledExperienceContext("server"),
        ControlledExperienceAccess("", Reviews()),
        HttpExperienceService(
            "http://experience:8000",
            "r" * 32,
            transport=httpx.MockTransport(experience),
        ),
        HttpExperienceAuthors(
            "http://user:8000", "u" * 32, transport=httpx.MockTransport(profiles)
        ),
    )
    app = FastAPI()

    async def close() -> None:
        """Тестовые адаптеры не открывают настоящую инфраструктуру."""

    app.state.container = ApplicationContainer(
        settings=Settings(
            _env_file=None,
            database=DatabaseSettings(password="test-only-password"),
            review=review,
            identity_proxy=identity,
        ),
        check_readiness=None,
        shutdown_callback=close,
        manage_review=object(),
        manage_experience=use_case,
    )
    app.state.identity_authorizer = IdentityAuthorizer(
        identity,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(introspect)
        ),
    )

    @app.middleware("http")
    async def authorization(request: Request, call_next):
        """Использует то же правило прав, что основной Gateway."""
        return await enforce_identity_authorization(
            request, app.state.identity_authorizer
        ) or await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "head-session")
        headers = {
            "X-PDRD-Review-Key": "f" * 32,
            "X-Review-Actor": "spoofed",
            "X-PDRD-Actor-Id": str(AUTHOR),
        }
        listed = client.get(
            "/api/v1/experience", params={"author": f"user:{AUTHOR}"}, headers=headers
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["author"]["display_name"] == "Иван Мейн"
        assert calls[-1].url.params["author"] == f"user:{AUTHOR}"
        authors = client.get("/api/v1/experience/authors", headers=headers)
        assert (
            authors.status_code == 200
            and authors.json()["items"][0]["author"]["login"] == "i.mein"
        )
        history = client.get(f"/api/v1/experience/{EXAMPLE}/history", headers=headers)
        assert history.status_code == 200
        before = len(calls)
        for method, path, command in (
            ("DELETE", f"/api/v1/experience/{EXAMPLE}", None),
            (
                "PATCH",
                f"/api/v1/experience/{EXAMPLE}",
                {"expected_revision": 0, "fields": {"text": "Подмена"}},
            ),
            (
                "POST",
                "/api/v1/experience/delete-selection",
                {"items": [{"id": str(EXAMPLE), "revision": 0}]},
            ),
        ):
            response = client.request(
                method,
                path,
                headers={**headers, "Origin": "https://pdrd.example.test"},
                json=command,
            )
            assert response.status_code == 403
        assert len(calls) == before and len(profile_calls) == 2
