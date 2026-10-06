# services/api-gateway/tests/functional/test_normative_section_permissions.py

"""Проверяет роли разделов и завершение выдачи доступа через настоящий HTTP Gateway."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeSectionView,
)
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.core.settings import DatabaseSettings, Settings
from pdrd_api_gateway.transport.http.identity_authorization import (
    IdentityAuthorizer,
    enforce_identity_authorization,
)
from pdrd_api_gateway.transport.http.routers.normative_catalog import router

SECTION, ACTOR = UUID(int=1), UUID(int=2)
ORIGIN = "https://pdrd.example.test"
NOW = datetime(2026, 10, 6, tzinfo=UTC)


class Catalog:
    """Имитирует прикладной фасад, фиксируя допущенные сервером изменения."""

    def __init__(self):
        """Создаёт неизменяемый результат и журнал операций."""
        self.calls = []
        self.section = NormativeSectionView(SECTION, "Раздел", "Промпт", NOW, NOW)

    async def create_section(self, *, name):
        """Фиксирует создание до последующей выдачи прав."""
        self.calls.append("create")
        return self.section

    async def update_section(self, *, section_id, changes):
        """Фиксирует переименование существующего раздела."""
        assert section_id == SECTION and changes == {"name": "Новое имя"}
        self.calls.append("rename")
        return self.section

    async def delete_section(self, *, section_id):
        """Подтверждает повторяемое удаление одного UUID."""
        assert section_id == SECTION
        self.calls.append("delete")
        return SECTION

    async def get_document(self, *, document_id):
        """Возвращает раздел документа для проверки области до удаления."""
        return SimpleNamespace(section_id=SECTION)

    async def delete_document(self, *, document_id):
        """Повторное удаление возвращает тот же UUID в контракте фасада."""
        self.calls.append("delete-document")
        return document_id


def build_app(role, *, grant_status=200, allowed_sections=(SECTION,)):
    """Подключает реальные маршруты и проверку сессии к управляемому private HTTP."""
    catalog, grants = Catalog(), []
    permissions = {
        "guest": [],
        "designer": [],
        "head": ["normative.write"],
        "admin": ["normative.write", "normative.delete", "admin.access"],
        "head_granted": ["normative.write", "normative.delete"],
        "designer_granted": ["normative.delete"],
    }[role]

    def private(request):
        """Служебные права и actor получаются из сессии, а не полей браузера."""
        if request.url.path.endswith("/introspect"):
            return httpx.Response(
                200, json={"user_id": str(ACTOR), "permissions": permissions}
            )
        assert request.headers["Authorization"] == "Bearer " + "u" * 32
        assert request.headers["X-PDRD-Actor-Id"] == str(ACTOR)
        if request.url.path.endswith("/section-access"):
            return httpx.Response(
                200,
                json={
                    "user_id": str(ACTOR),
                    "all_sections": False,
                    "section_ids": [str(item) for item in allowed_sections],
                },
            )
        assert request.url.path == "/internal/v1/users/section-catalog/grants"
        grants.append(request)
        return httpx.Response(
            grant_status,
            json={
                "section_id": str(SECTION),
                "granted_users": 2,
                "already_distributed": False,
            },
        )

    authorizer = IdentityAuthorizer(
        IdentityProxySettings(
            enabled=True,
            authorization_enabled=True,
            public_origin=ORIGIN,
            trusted_proxy_key="p" * 32,
            technical_assignment_access_key="t" * 32,
            auth_internal_key="a" * 32,
            user_service_internal_key="u" * 32,
        ),
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(private)
        ),
    )
    app = FastAPI()

    async def close():
        """Управляемый фасад не создаёт соединений с внешними сервисами."""

    app.state.container = ApplicationContainer(
        settings=Settings(
            _env_file=None,
            database=DatabaseSettings(password="section-http-test-password"),
        ),
        check_readiness=None,
        shutdown_callback=close,
        normative_catalog=catalog,
    )
    app.state.identity_authorizer = authorizer

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        """Закрывает действие до вызова прикладного фасада, как рабочий Gateway."""
        return await enforce_identity_authorization(
            request, authorizer
        ) or await call_next(request)

    app.include_router(router)
    return app, catalog, grants


@pytest.mark.parametrize(
    "role", ["guest", "designer", "head", "admin", "head_granted", "designer_granted"]
)
@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
def test_sections_preserve_head_write_and_require_separate_delete_permission(
    role, operation
):
    """Фронтенд не может открыть запрещённую операцию прямым HTTP-запросом."""
    app, catalog, grants = build_app(role)
    expected = (
        role in {"admin", "head_granted"}
        or (role == "head" and operation != "delete")
        or (role == "designer_granted" and operation == "delete")
    )
    with TestClient(app) as client:
        if role != "guest":
            client.cookies.set("pdrd_session", "verified-session")
        headers = {"Origin": ORIGIN, "X-PDRD-Actor-Id": str(UUID(int=99))}
        if operation == "create":
            response = client.post(
                "/api/v1/normative/sections", json={"name": "Раздел"}, headers=headers
            )
        elif operation == "rename":
            response = client.patch(
                f"/api/v1/normative/sections/{SECTION}",
                json={"name": "Новое имя"},
                headers=headers,
            )
        else:
            response = client.delete(
                f"/api/v1/normative/sections/{SECTION}", headers=headers
            )
        expected_status = (
            (201 if operation == "create" else 200)
            if expected
            else (401 if role == "guest" else 403)
        )
        assert response.status_code == expected_status
        assert catalog.calls == ([operation] if expected else [])
        assert len(grants) == int(expected and operation == "create")


def test_created_section_reports_distribution_failure_with_section_id():
    """Ошибка выдачи прав не превращается в ложный 201 или повтор создания раздела."""
    app, catalog, grants = build_app("head", grant_status=503)
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "verified-session")
        response = client.post(
            "/api/v1/normative/sections",
            json={"name": "Раздел"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 503
        assert response.json()["section_id"] == str(SECTION)
        assert isinstance(response.json()["detail"], str)
        assert catalog.calls == ["create"] and len(grants) == 1


@pytest.mark.parametrize("role", ["head_granted", "designer_granted"])
def test_explicit_deletion_still_requires_assigned_section(role):
    """Галочка не открывает удаление разделов вне назначенной пользователю области."""
    app, catalog, _ = build_app(role, allowed_sections=())
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "verified-session")
        response = client.delete(
            f"/api/v1/normative/sections/{SECTION}", headers={"Origin": ORIGIN}
        )
        assert response.status_code == 403
    assert catalog.calls == []


@pytest.mark.parametrize(
    "role", ["designer", "head", "designer_granted", "head_granted", "admin"]
)
@pytest.mark.parametrize("assigned", [False, True])
def test_document_deletion_requires_both_permission_and_section_scope(role, assigned):
    """Назначенное удаление проверяется отдельно от создания и доступа к разделу."""
    app, catalog, _ = build_app(role, allowed_sections=(SECTION,) if assigned else ())
    allowed = role == "admin" or (
        assigned and role in {"designer_granted", "head_granted"}
    )
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "verified-session")
        for _ in range(2):
            response = client.delete(
                f"/api/v1/normative/documents/{UUID(int=3)}", headers={"Origin": ORIGIN}
            )
            assert response.status_code == (200 if allowed else 403)
    assert catalog.calls == (["delete-document"] * 2 if allowed else [])
