# services/api-gateway/tests/integration/test_user_package_endpoints.py

"""HTTP contract tests public user-package catalog."""

from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from datetime import (
    UTC,
    datetime,
)
from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCategoryView,
    NormativeDocumentContent,
    NormativeDocumentView,
)
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.application.use_cases.manage_user_packages import (
    UserPackageCatalogFacade,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_api_gateway.main import create_app

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

CATEGORY_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

DOCUMENT_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

NOW = datetime(
    2026,
    9,
    4,
    7,
    0,
    tzinfo=UTC,
)


class StaticReadiness:
    """Fake readiness dependency."""

    async def is_ready(
        self,
    ) -> bool:
        """Всегда ready."""
        return True


class FakeUserPackageCatalogManager:
    """In-memory fake user-package manager."""

    def __init__(
        self,
    ) -> None:
        """Создаёт predictable state."""
        self.category = NormativeCategoryView(
            category_id=CATEGORY_ID,
            section_id=SECTION_ID,
            parent_id=None,
            name="Пакет заказчика",
            created_at=NOW,
            updated_at=NOW,
        )

        self.document = NormativeDocumentView(
            document_id=DOCUMENT_ID,
            section_id=SECTION_ID,
            category_id=CATEGORY_ID,
            original_name="package.pdf",
            mime_type="application/pdf",
            size_bytes=12,
            index_status="uploaded",
            index_error=None,
            indexed_at=None,
            ready_for_analysis=False,
            created_at=NOW,
            updated_at=NOW,
        )

        self.uploaded_content = b""

        self.category_changes: dict[
            str,
            object,
        ] = {}

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает package category."""
        assert section_id == SECTION_ID

        return (self.category,)

    async def create_category(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategoryView:
        """Создаёт package category."""
        return NormativeCategoryView(
            category_id=CATEGORY_ID,
            section_id=section_id,
            parent_id=parent_id,
            name=name,
            created_at=NOW,
            updated_at=NOW,
        )

    async def get_category(
        self,
        *,
        category_id: UUID,
    ) -> NormativeCategoryView:
        """Возвращает package category."""
        assert category_id == CATEGORY_ID

        return self.category

    async def update_category(
        self,
        *,
        category_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeCategoryView:
        """Изменяет package category."""
        assert category_id == CATEGORY_ID

        self.category_changes = dict(
            changes,
        )

        return self.category

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет package category."""
        return category_id

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает package documents."""
        assert section_id == SECTION_ID

        return (self.document,)

    async def upload_document(
        self,
        *,
        section_id: UUID,
        category_id: UUID | None,
        original_name: str,
        content: bytes,
        content_type: str,
    ) -> NormativeDocumentView:
        """Сохраняет forwarded package upload."""
        assert section_id == SECTION_ID

        assert category_id == CATEGORY_ID

        assert original_name == "package.pdf"

        assert content_type == "application/pdf"

        self.uploaded_content = content

        return self.document

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает package document."""
        assert document_id == DOCUMENT_ID

        return self.document

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Перемещает package document."""
        assert document_id == DOCUMENT_ID

        return NormativeDocumentView(
            document_id=DOCUMENT_ID,
            section_id=SECTION_ID,
            category_id=category_id,
            original_name=self.document.original_name,
            mime_type=self.document.mime_type,
            size_bytes=self.document.size_bytes,
            index_status=self.document.index_status,
            index_error=None,
            indexed_at=None,
            ready_for_analysis=False,
            created_at=NOW,
            updated_at=NOW,
        )

    async def delete_document(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет package document."""
        return document_id

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает queued package document."""
        assert document_id == DOCUMENT_ID

        return NormativeDocumentView(
            document_id=DOCUMENT_ID,
            section_id=SECTION_ID,
            category_id=CATEGORY_ID,
            original_name=self.document.original_name,
            mime_type=self.document.mime_type,
            size_bytes=self.document.size_bytes,
            index_status="queued",
            index_error=None,
            indexed_at=None,
            ready_for_analysis=False,
            created_at=NOW,
            updated_at=NOW,
        )

    async def get_document_content(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Возвращает fake PDF."""
        assert document_id == DOCUMENT_ID

        return NormativeDocumentContent(
            content=b"%PDF-package-public",
            mime_type="application/pdf",
        )


async def noop_shutdown() -> None:
    """Имитирует shutdown."""


def build_client(
    manager: FakeUserPackageCatalogManager,
) -> TestClient:
    """Создаёт Gateway HTTP client с fake package manager."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database=DatabaseSettings(
            password="test-password",
        ),
    )

    check_readiness = CheckReadiness(
        database=StaticReadiness(),
        broker=StaticReadiness(),
    )

    shutdown_callback: Callable[
        [],
        Awaitable[None],
    ] = noop_shutdown

    container = ApplicationContainer(
        settings=settings,
        check_readiness=check_readiness,
        shutdown_callback=shutdown_callback,
        user_package_catalog=UserPackageCatalogFacade(
            manager=manager,
        ),
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_public_user_package_category_contract() -> None:
    """Gateway публикует CRUD package categories."""
    manager = FakeUserPackageCatalogManager()

    with build_client(
        manager,
    ) as client:
        list_response = client.get(
            (f"/api/v1/normative/sections/{SECTION_ID}/user-packages/categories"),
        )

        create_response = client.post(
            (f"/api/v1/normative/sections/{SECTION_ID}/user-packages/categories"),
            json={
                "name": "Дополнительный пакет",
                "parent_id": None,
            },
        )

        patch_response = client.patch(
            (f"/api/v1/normative/user-packages/categories/{CATEGORY_ID}"),
            json={
                "name": "Новое имя",
            },
        )

        delete_response = client.delete(
            (f"/api/v1/normative/user-packages/categories/{CATEGORY_ID}"),
        )

    assert list_response.status_code == 200

    assert list_response.json()[0]["category_id"] == str(
        CATEGORY_ID,
    )

    assert create_response.status_code == 201

    assert create_response.json()["name"] == ("Дополнительный пакет")

    assert patch_response.status_code == 200

    assert manager.category_changes == {
        "name": "Новое имя",
    }

    assert delete_response.status_code == 200


def test_public_user_package_document_lifecycle() -> None:
    """Gateway публикует package upload/index/move/content/delete."""
    manager = FakeUserPackageCatalogManager()

    with build_client(
        manager,
    ) as client:
        list_response = client.get(
            (f"/api/v1/normative/sections/{SECTION_ID}/user-packages/documents"),
        )

        upload_response = client.post(
            (f"/api/v1/normative/sections/{SECTION_ID}/user-packages/documents"),
            files={
                "file": (
                    "package.pdf",
                    b"%PDF-package",
                    "application/pdf",
                ),
            },
            data={
                "category_id": str(
                    CATEGORY_ID,
                ),
            },
        )

        queue_response = client.post(
            (f"/api/v1/normative/user-packages/documents/{DOCUMENT_ID}/index"),
        )

        move_response = client.patch(
            (f"/api/v1/normative/user-packages/documents/{DOCUMENT_ID}"),
            json={
                "category_id": None,
            },
        )

        content_response = client.get(
            (f"/api/v1/normative/user-packages/documents/{DOCUMENT_ID}/content"),
        )

        delete_response = client.delete(
            (f"/api/v1/normative/user-packages/documents/{DOCUMENT_ID}"),
        )

    assert list_response.status_code == 200

    assert upload_response.status_code == 201

    assert manager.uploaded_content == b"%PDF-package"

    assert queue_response.status_code == 202

    assert queue_response.json()["index_status"] == "queued"

    assert move_response.status_code == 200

    assert move_response.json()["category_id"] is None

    assert content_response.status_code == 200

    assert content_response.content == b"%PDF-package-public"

    assert content_response.headers["content-type"].startswith(
        "application/pdf",
    )

    assert delete_response.status_code == 200
