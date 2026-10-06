# services/api-gateway/tests/unit/test_identity_authorization.py

"""Регрессия серверных прав при включённом профиле auth."""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pdrd_api_gateway.application.use_cases.manage_experience import ManageExperience
from pdrd_api_gateway.application.use_cases.manage_review import ManageReview
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.infrastructure.experience import ControlledExperienceContext
from pdrd_api_gateway.infrastructure.review import ControlledReviewContext
from pdrd_api_gateway.transport.http.identity_authorization import (
    IdentityAuthorizer,
    VerifiedIdentity,
    enforce_identity_authorization,
    required_permission,
)
from pdrd_api_gateway.transport.http.request_actor import authenticated_actor
from pdrd_api_gateway.transport.http.routers.normative_catalog import _visible_section
from pdrd_api_gateway.transport.http.schemas.normative_catalog import (
    NormativeSectionResponse,
)
from pydantic import SecretStr, ValidationError
from starlette.requests import Request


def settings() -> IdentityProxySettings:
    """Готовит безопасный тестовый внутренний канал."""
    return IdentityProxySettings(
        enabled=True,
        authorization_enabled=True,
        auth_internal_key=SecretStr("k" * 32),
        user_service_internal_key=SecretStr("u" * 32),
        technical_assignment_access_key=SecretStr("t" * 32),
        trusted_proxy_key=SecretStr("p" * 32),
        public_origin="https://pdrd.example",
    )


def request(
    method: str,
    path: str,
    *,
    cookie: str = "pdrd_session=opaque",
    origin: str = "https://pdrd.example",
    body: bytes = b"",
) -> Request:
    """Создаёт ASGI-запрос без браузера и рабочей сети."""
    headers = [(b"cookie", cookie.encode()), (b"origin", origin.encode())]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "https",
        "headers": headers,
        "query_string": b"",
        "server": ("pdrd.example", 443),
    }

    async def receive() -> dict[str, object]:
        """Поставляет тело только в память теста."""
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def authorizer(permissions: list[str]) -> IdentityAuthorizer:
    """Имитирует ответ Auth Service, сохраняя проверку служебного ключа."""

    def reply(received: httpx.Request) -> httpx.Response:
        """Убеждается, что Gateway не доверяет клиентским правам."""
        assert received.headers["authorization"] == f"Bearer {'k' * 32}"
        assert received.url.path == "/internal/v1/auth/introspect"
        assert json.loads(received.content)["token"] == "opaque"
        return httpx.Response(
            200,
            json={
                "permissions": permissions,
                "user_id": "72b1bb21-0ff2-4382-87a3-7848fdb2a91c",
            },
        )

    return IdentityAuthorizer(
        settings(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)),
    )


def test_policy_keeps_guest_analysis_and_restricts_mutations() -> None:
    """Гость анализирует, но не меняет нормативы, Review и Experience."""
    assert required_permission("POST", "/api/v1/analyses") is None
    assert (
        required_permission("POST", "/api/v1/normative/technical-assignments") is None
    )
    assert required_permission("GET", "/api/v1/normative/sections") is None
    assert required_permission("POST", "/api/v1/normative/sections") == (
        "normative.write",
    )
    assert required_permission("DELETE", "/api/v1/normative/sections/id") == (
        "normative.delete",
    )
    assert required_permission(
        "PATCH", "/api/v1/normative/sections/id", {"system_prompt": "x"}
    ) == ("system_prompt.manage",)
    assert required_permission(
        "POST", "/api/v1/analyses/id/review/commands", {"action": "add"}
    ) == ("review.gold.create",)
    assert required_permission(
        "POST", "/api/v1/analyses/id/review/commands", {"action": "approve"}
    ) == ("review.approve",)
    assert required_permission(
        "POST",
        "/api/v1/analyses/id/review/commands",
        {"action": "edit", "finding_id": "manual:123"},
    ) == ("review.gold.create",)
    assert required_permission(
        "POST",
        "/api/v1/analyses/id/review/commands",
        {"action": "edit", "finding_id": "vlm-123"},
    ) == ("review.findings.decide",)
    assert required_permission("POST", "/api/v1/experience/capture/id") == (
        "experience.capture",
    )
    assert required_permission("GET", "/api/v1/experience") == (
        "experience.catalog.read",
    )
    assert required_permission("GET", "/api/v1/experience/id") == (
        "experience.catalog.read",
    )
    assert required_permission("GET", "/api/v1/experience/export") == (
        "experience.catalog.read",
    )
    assert required_permission("GET", "/api/v1/experience-versions") == (
        "admin.access",
    )
    assert required_permission("GET", "/api/v1/experience-versions/id/dataset") == (
        "admin.access",
    )


@pytest.mark.asyncio
async def test_authorization_checks_cookie_role_and_origin() -> None:
    """Чужой Origin и отсутствие права не проходят даже при видимой кнопке UI."""
    subject = authorizer(["normative.write"])
    assert (
        await subject.require(
            request("POST", "/api/v1/normative/sections"), ("normative.write",)
        )
        is None
    )
    missing = await subject.require(
        request("POST", "/api/v1/normative/sections", cookie=""),
        ("normative.write",),
    )
    assert missing.status_code == 401
    wrong_origin = await subject.require(
        request("POST", "/api/v1/normative/sections", origin="https://evil.example"),
        ("normative.write",),
    )
    assert wrong_origin.status_code == 403
    no_right = await subject.require(
        request("POST", "/api/v1/experience/capture/id"), ("experience.capture",)
    )
    assert no_right.status_code == 403


@pytest.mark.asyncio
async def test_review_action_is_read_from_json_before_role_check() -> None:
    """Проектировщик может добавить Gold, но не утвердить тот же Review."""
    subject = authorizer(["review.gold.create"])
    path = "/api/v1/analyses/id/review/commands"
    add = await enforce_identity_authorization(
        request("POST", path, body=b'{"action":"add"}'), subject
    )
    approve = await enforce_identity_authorization(
        request("POST", path, body=b'{"action":"approve"}'), subject
    )
    assert add is None
    assert approve.status_code == 403


@pytest.mark.asyncio
async def test_public_analysis_requires_same_origin_for_upload() -> None:
    """Гостевой анализ не запускается через форму постороннего сайта."""
    subject = authorizer([])
    denied = await enforce_identity_authorization(
        request("POST", "/api/v1/analyses", cookie="", origin="https://evil.example"),
        subject,
    )
    allowed = await enforce_identity_authorization(
        request("POST", "/api/v1/analyses", cookie=""), subject
    )
    assert denied is not None and denied.status_code == 403
    assert allowed is None


def test_authorization_settings_fail_without_gateway_and_secret() -> None:
    """Включённая политика не стартует без закрытого Auth Service."""
    with pytest.raises(ValidationError):
        IdentityProxySettings(authorization_enabled=True)
    with pytest.raises(ValidationError):
        IdentityProxySettings(
            enabled=True,
            authorization_enabled=True,
            public_origin="https://pdrd.example",
        )
    with pytest.raises(ValidationError):
        IdentityProxySettings(enabled=True)
    for origin in (
        "http://pdrd.example",
        "https://pdrd.example/path",
        "https://pdrd.example:invalid",
        "https://",
    ):
        with pytest.raises(ValidationError):
            IdentityProxySettings(
                enabled=True,
                authorization_enabled=True,
                auth_internal_key=SecretStr("k" * 32),
                user_service_internal_key=SecretStr("u" * 32),
                technical_assignment_access_key=SecretStr("t" * 32),
                trusted_proxy_key=SecretStr("p" * 32),
                public_origin=origin,
            )


def test_normative_prompt_is_not_returned_to_guest_or_non_admin() -> None:
    """Скрытый в UI системный промпт также не выдаётся через открытый API."""
    http_request = request("GET", "/api/v1/normative/sections")
    http_request.scope["app"] = SimpleNamespace(
        state=SimpleNamespace(identity_authorizer=object())
    )
    section = NormativeSectionResponse.model_construct(system_prompt="secret prompt")
    assert _visible_section(section, http_request).system_prompt == ""
    http_request.state.verified_identity = VerifiedIdentity(
        UUID("72b1bb21-0ff2-4382-87a3-7848fdb2a91c"),
        frozenset({"normative.write"}),
    )
    assert _visible_section(section, http_request).system_prompt == ""
    http_request.state.verified_identity = VerifiedIdentity(
        UUID("72b1bb21-0ff2-4382-87a3-7848fdb2a91c"),
        frozenset({"system_prompt.manage"}),
    )
    assert _visible_section(section, http_request).system_prompt == "secret prompt"


@pytest.mark.asyncio
async def test_review_and_experience_audit_the_verified_session_actor() -> None:
    """Старый технический actor не записывается в историю операций вошедшего человека."""
    observed: list[str] = []

    class Access:
        """Сохраняет субъект каждой операции до записи в соседний сервис."""

        async def require(self, context: object) -> None:
            """Фиксирует actor из контекста приложения."""
            observed.append(context.actor)

    class ReviewService:
        """Подменяет внешний Review для проверки переданного actor."""

        async def execute(self, *, context: object, command: object = None) -> dict:
            """Возвращает только идентификатор проверенного оператора."""
            return {"actor": context.actor}

    class ExperienceService:
        """Подменяет внешний Experience для проверки того же actor."""

        async def execute(self, *, context: object, **options: object) -> dict:
            """Возвращает actor без обращения к сети."""
            return {"actor": context.actor}

    actor = f"user:{uuid4()}"
    review = ManageReview(ControlledReviewContext("legacy"), Access(), ReviewService())
    experience = ManageExperience(
        ControlledExperienceContext("legacy"), Access(), ExperienceService()
    )
    assert (await review.execute(job_id=uuid4(), operation="read", actor=actor))[
        "actor"
    ] == actor
    assert (await experience.execute(operation="list", actor=actor))["actor"] == actor
    assert observed == [actor, actor]


def test_request_actor_only_uses_server_verified_state() -> None:
    """Браузерный X-Review-Actor не заменяет ID из Auth introspect."""
    http_request = request("GET", "/api/v1/experience")
    http_request.scope["app"] = SimpleNamespace(
        state=SimpleNamespace(identity_authorizer=object())
    )
    http_request.scope["headers"].append((b"x-review-actor", b"forged"))
    with pytest.raises(Exception, match="Требуется вход"):
        authenticated_actor(http_request)
    user_id = uuid4()
    http_request.state.identity_user_id = user_id
    assert authenticated_actor(http_request) == f"user:{user_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream", ["success", "unavailable", "wrong-section", "invalid-count"]
)
async def test_created_section_distribution_uses_verified_actor_and_validates_result(
    upstream,
):
    """Браузерный актёр не влияет на RPC, а неполная выдача доступа не становится успехом."""
    section_id, actor_id, browser_id = uuid4(), uuid4(), uuid4()

    def reply(received: httpx.Request) -> httpx.Response:
        """Проверяет служебный канал и имитирует разные ответы User Service."""
        assert received.url.path == "/internal/v1/users/section-catalog/grants"
        assert received.headers["Authorization"] == "Bearer " + "u" * 32
        assert received.headers["X-PDRD-Actor-Id"] == str(actor_id)
        assert json.loads(received.content) == {"section_id": str(section_id)}
        if upstream == "unavailable":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "section_id": str(
                    uuid4() if upstream == "wrong-section" else section_id
                ),
                "granted_users": True if upstream == "invalid-count" else 3,
                "already_distributed": False,
            },
        )

    subject = IdentityAuthorizer(
        settings(),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)),
    )
    current = request(
        "POST",
        "/api/v1/normative/sections",
        body=json.dumps({"actor_user_id": str(browser_id)}).encode(),
    )
    current.state.verified_identity = VerifiedIdentity(
        actor_id, frozenset({"normative.write"})
    )
    current.state.allowed_section_ids = frozenset()
    result = await subject.distribute_created_section(current, section_id)
    assert (result is None) is (upstream == "success")
    if result is not None:
        assert result.status_code == 503
        assert json.loads(result.body)["section_id"] == str(section_id)
    else:
        assert current.state.allowed_section_ids is None
