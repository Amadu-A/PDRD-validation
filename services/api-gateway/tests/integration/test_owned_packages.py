# services/api-gateway/tests/integration/test_owned_packages.py

"""Проверяет ownership в настоящем Gateway и HTTP адаптере приватного каталога."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pdrd_api_gateway.application.ports.normative_catalog import NormativeSectionRecord
from pdrd_api_gateway.application.use_cases.manage_user_packages import (
    UserPackageCatalogFacade,
)
from pdrd_api_gateway.application.use_cases.resolve_normative_snapshot import (
    InvalidNormativeSelectionError,
    ResolveNormativeSnapshot,
)
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    KnowledgeServiceSettings,
    Settings,
)
from pdrd_api_gateway.infrastructure.knowledge.user_package_catalog import (
    HttpUserPackageCatalogManager,
)
from pdrd_api_gateway.main import create_app
from pdrd_api_gateway.transport.http.identity_authorization import IdentityAuthorizer
from pydantic import SecretStr


class Catalog:
    """Имитирует приватный каталог с двумя владельцами, включая данные чужого UUID."""

    def __init__(self) -> None:
        """Создаёт отдельные идентификаторы пользователей, раздела и документов."""
        self.owner = uuid4()
        self.other = uuid4()
        self.section = uuid4()
        self.document = uuid4()
        self.foreign_document = uuid4()
        self.category = uuid4()
        self.calls = []

    def payload(self, identifier: UUID, owner: UUID, *, category: bool = False) -> dict:
        """Возвращает валидные метаданные с неизменяемым владельцем."""
        base = {
            "section_id": str(self.section),
            "owner_user_id": str(owner),
            "area": "user_package",
            "created_at": "2026-10-05T10:00:00Z",
            "updated_at": "2026-10-05T10:00:00Z",
        }
        if category:
            return {
                **base,
                "category_id": str(identifier),
                "parent_id": None,
                "name": "Личный пакет",
            }
        return {
            **base,
            "document_id": str(identifier),
            "category_id": None,
            "original_name": "project.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "index_status": "ready",
            "index_error": None,
            "indexed_at": "2026-10-05T10:00:00Z",
            "ready_for_analysis": True,
        }

    def reply(self, request: httpx.Request) -> httpx.Response:
        """Проверяет передачу владельца; GET чужого UUID специально не фильтрует."""
        self.calls.append(request)
        assert request.headers["X-PDRD-Package-Owner"] == str(self.owner)
        path = request.url.path
        if path.endswith("/documents"):
            assert request.url.params["owner_user_id"] == str(self.owner)
            return httpx.Response(200, json=[self.payload(self.document, self.owner)])
        if path.endswith("/categories"):
            if request.method == "POST":
                assert json.loads(request.content)["owner_user_id"] == str(self.owner)
                return httpx.Response(
                    201, json=self.payload(self.category, self.owner, category=True)
                )
            assert request.url.params["owner_user_id"] == str(self.owner)
            return httpx.Response(
                200, json=[self.payload(self.category, self.owner, category=True)]
            )
        if str(self.foreign_document) in path:
            assert request.method == "GET", (
                "Чужое содержимое или мутация не должны достигать Knowledge"
            )
            return httpx.Response(
                200, json=self.payload(self.foreign_document, self.other)
            )
        return httpx.Response(200, json=self.payload(self.document, self.owner))

    def manager(self) -> HttpUserPackageCatalogManager:
        """Создаёт реальный HTTP адаптер с контролируемым приватным сервером."""
        return HttpUserPackageCatalogManager(
            settings=KnowledgeServiceSettings(base_url="http://knowledge.test"),
            transport=httpx.MockTransport(self.reply),
        )


def build_client(monkeypatch: pytest.MonkeyPatch, catalog: Catalog) -> TestClient:
    """Включает настоящий middleware Auth и повторную проверку назначений User Service."""

    def identity_reply(request: httpx.Request) -> httpx.Response:
        """Подтверждает текущую сессию и доступ только к одному разделу."""
        if request.url.path.endswith("/section-access"):
            assert request.headers["X-PDRD-Actor-Id"] == str(catalog.owner)
            return httpx.Response(
                200,
                json={
                    "user_id": str(catalog.owner),
                    "all_sections": False,
                    "section_ids": [str(catalog.section)],
                },
            )
        return httpx.Response(
            200,
            json={
                "user_id": str(catalog.owner),
                "permissions": [
                    "user_documents.own.read",
                    "user_documents.own.write",
                    "working_prompt.use",
                    "system_prompt.read",
                ],
            },
        )

    async def close() -> None:
        """Закрывает контейнер без сети и БД."""

    monkeypatch.setattr(
        IdentityAuthorizer,
        "_new_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(identity_reply)),
    )
    settings = Settings(
        _env_file=None,
        database=DatabaseSettings(password="test-only"),
        identity_proxy=IdentityProxySettings(
            enabled=True,
            authorization_enabled=True,
            auth_internal_key=SecretStr("a" * 32),
            user_service_internal_key=SecretStr("u" * 32),
            technical_assignment_access_key=SecretStr("t" * 32),
            trusted_proxy_key=SecretStr("p" * 32),
            public_origin="https://pdrd.test",
        ),
    )
    container = ApplicationContainer(
        settings=settings,
        check_readiness=None,
        shutdown_callback=close,
        user_package_catalog=UserPackageCatalogFacade(catalog.manager()),
    )
    client = TestClient(create_app(container))
    client.cookies.set("pdrd_session", "owner")
    return client


def test_owner_catalog_ignores_browser_owner_and_blocks_foreign_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Браузер не меняет владельца параметром, заголовком или чужим UUID документа."""
    catalog = Catalog()
    with build_client(monkeypatch, catalog) as client:
        response = client.get(
            f"/api/v1/normative/sections/{catalog.section}/user-packages/documents?owner_user_id={catalog.other}",
            headers={"X-PDRD-Package-Owner": str(catalog.other)},
        )
        assert response.status_code == 200
        assert [item["document_id"] for item in response.json()] == [
            str(catalog.document)
        ]
        for suffix in ("", "/content"):
            denied = client.get(
                f"/api/v1/normative/user-packages/documents/{catalog.foreign_document}{suffix}"
            )
            assert denied.status_code == 404
        denied = client.delete(
            f"/api/v1/normative/user-packages/documents/{catalog.foreign_document}",
            headers={"Origin": "https://pdrd.test"},
        )
        assert denied.status_code == 404
        denied = client.get(
            f"/api/v1/normative/sections/{uuid4()}/user-packages/documents"
        )
        assert denied.status_code == 403
        created = client.post(
            f"/api/v1/normative/sections/{catalog.section}/user-packages/categories",
            json={"name": "Личный пакет", "parent_id": None},
            headers={"Origin": "https://pdrd.test"},
        )
        assert created.status_code == 201
        rejected = client.post(
            f"/api/v1/normative/sections/{catalog.section}/user-packages/categories",
            json={"name": "Подмена", "owner_user_id": str(catalog.other)},
            headers={"Origin": "https://pdrd.test"},
        )
        assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_snapshot_accepts_only_owned_selected_documents() -> None:
    """Проверка владельца происходит до фиксации ID и рабочего промпта снимка."""
    catalog = Catalog()

    class Normative:
        """Подменяет общий каталог, сохраняя настоящую проверку личных документов."""

        async def get_section(self, *, section_id: UUID) -> NormativeSectionRecord:
            """Возвращает текущий системный промпт выбранного раздела."""
            return NormativeSectionRecord(
                section_id=section_id, system_prompt="Системный"
            )

        async def list_documents(self, *, section_id: UUID) -> tuple:
            """Не добавляет нормативные документы в личный selection."""
            return ()

    resolver = ResolveNormativeSnapshot(Normative(), catalog.manager())
    args = {
        "section_id": catalog.section,
        "owner_user_id": catalog.owner,
        "document_ids": (),
        "prompt_override_enabled": True,
        "prompt_override": "Рабочий",
    }
    snapshot = await resolver.execute(
        **args, user_package_document_ids=(catalog.document,)
    )
    assert snapshot.user_package_document_ids == (catalog.document,)
    assert snapshot.document_ids == ()
    assert snapshot.system_prompt == "Рабочий"
    with pytest.raises(InvalidNormativeSelectionError):
        await resolver.execute(
            **args, user_package_document_ids=(catalog.foreign_document,)
        )
