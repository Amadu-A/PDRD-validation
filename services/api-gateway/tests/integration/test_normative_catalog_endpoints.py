# services/api-gateway/tests/integration/test_normative_catalog_endpoints.py

"""HTTP contract tests public normative catalog facade."""

from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from datetime import (
    UTC,
    datetime,
)
from uuid import (
    UUID,
    uuid4,
)

import pytest
from fastapi.testclient import TestClient
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogConflictError,
    NormativeCatalogNotFoundError,
    NormativeCatalogUnavailableError,
    NormativeCatalogValidationError,
    NormativeCategoryView,
    NormativeDocumentContent,
    NormativeDocumentView,
    NormativeSectionView,
)
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.application.use_cases.manage_normative_catalog import (
    NormativeCatalogFacade,
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
    3,
    8,
    0,
    tzinfo=UTC,
)


class StaticReadiness:
    """Fake readiness dependency."""

    async def is_ready(
        self,
    ) -> bool:
        """Всегда сообщает readiness."""
        return True


class FakeNormativeCatalogManager:
    """In-memory fake normative catalog manager."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует predictable test state."""
        self.section = NormativeSectionView(
            section_id=SECTION_ID,
            name="ЭОМ",
            system_prompt="Initial prompt.",
            created_at=NOW,
            updated_at=NOW,
        )

        self.category = NormativeCategoryView(
            category_id=CATEGORY_ID,
            section_id=SECTION_ID,
            parent_id=None,
            name="Кабельные линии",
            created_at=NOW,
            updated_at=NOW,
        )

        self.document = NormativeDocumentView(
            document_id=DOCUMENT_ID,
            section_id=SECTION_ID,
            category_id=CATEGORY_ID,
            original_name="norm.pdf",
            mime_type="application/pdf",
            size_bytes=11,
            index_status="uploaded",
            index_error=None,
            indexed_at=None,
            ready_for_analysis=False,
            created_at=NOW,
            updated_at=NOW,
        )

        self.section_changes: dict[
            str,
            object,
        ] = {}

        self.category_changes: dict[
            str,
            object,
        ] = {}

        self.uploaded_content = b""

        self.failure: Exception | None = None

    def _raise_failure(
        self,
    ) -> None:
        """Поднимает configured application error."""
        if self.failure is not None:
            raise self.failure

    async def list_sections(
        self,
    ) -> tuple[
        NormativeSectionView,
        ...,
    ]:
        """Возвращает section."""
        self._raise_failure()

        return (self.section,)

    async def create_section(
        self,
        *,
        name: str,
    ) -> NormativeSectionView:
        """Создаёт test section."""
        self._raise_failure()

        return NormativeSectionView(
            section_id=SECTION_ID,
            name=name,
            system_prompt="Default prompt.",
            created_at=NOW,
            updated_at=NOW,
        )

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionView:
        """Возвращает section."""
        self._raise_failure()

        assert section_id == SECTION_ID

        return self.section

    async def update_section(
        self,
        *,
        section_id: UUID,
        changes: Mapping[
            str,
            object,
        ],
    ) -> NormativeSectionView:
        """Сохраняет PATCH section."""
        self._raise_failure()

        assert section_id == SECTION_ID

        self.section_changes = dict(
            changes,
        )

        return NormativeSectionView(
            section_id=SECTION_ID,
            name=str(
                changes.get(
                    "name",
                    self.section.name,
                )
            ),
            system_prompt=str(
                changes.get(
                    "system_prompt",
                    self.section.system_prompt,
                )
            ),
            created_at=NOW,
            updated_at=NOW,
        )

    async def delete_section(
        self,
        *,
        section_id: UUID,
    ) -> UUID:
        """Удаляет section."""
        self._raise_failure()

        return section_id

    async def list_categories(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeCategoryView,
        ...,
    ]:
        """Возвращает categories."""
        self._raise_failure()

        assert section_id == SECTION_ID

        return (self.category,)

    async def create_category(
        self,
        *,
        section_id: UUID,
        name: str,
        parent_id: UUID | None,
    ) -> NormativeCategoryView:
        """Создаёт category."""
        self._raise_failure()

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
        """Возвращает category."""
        self._raise_failure()

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
        """Сохраняет PATCH category."""
        self._raise_failure()

        assert category_id == CATEGORY_ID

        self.category_changes = dict(
            changes,
        )

        return NormativeCategoryView(
            category_id=CATEGORY_ID,
            section_id=SECTION_ID,
            parent_id=changes.get(
                "parent_id",
                self.category.parent_id,
            ),
            name=str(
                changes.get(
                    "name",
                    self.category.name,
                )
            ),
            created_at=NOW,
            updated_at=NOW,
        )

    async def delete_category(
        self,
        *,
        category_id: UUID,
    ) -> UUID:
        """Удаляет category."""
        self._raise_failure()

        return category_id

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает documents."""
        self._raise_failure()

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
        """Сохраняет forwarded upload."""
        self._raise_failure()

        assert section_id == SECTION_ID

        assert category_id == CATEGORY_ID

        assert original_name == "norm.pdf"

        assert content_type == "application/pdf"

        self.uploaded_content = content

        return self.document

    async def get_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает document."""
        self._raise_failure()

        assert document_id == DOCUMENT_ID

        return self.document

    async def move_document(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocumentView:
        """Возвращает document после move."""
        self._raise_failure()

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
        """Удаляет document."""
        self._raise_failure()

        return document_id

    async def queue_document(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentView:
        """Возвращает queued document."""
        self._raise_failure()

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
        self._raise_failure()

        assert document_id == DOCUMENT_ID

        return NormativeDocumentContent(
            content=b"%PDF-public-facade",
            mime_type="application/pdf",
        )


async def noop_shutdown() -> None:
    """Имитирует shutdown."""


def build_client(
    manager: FakeNormativeCatalogManager,
) -> TestClient:
    """Создаёт Gateway HTTP client с fake manager."""
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
        normative_catalog=NormativeCatalogFacade(
            manager=manager,
        ),
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_public_section_contract() -> None:
    """Публичный Gateway управляет sections."""
    manager = FakeNormativeCatalogManager()

    with build_client(
        manager,
    ) as client:
        list_response = client.get(
            "/api/v1/normative/sections",
        )

        create_response = client.post(
            "/api/v1/normative/sections",
            json={
                "name": "ОВ",
            },
        )

        patch_response = client.patch(
            f"/api/v1/normative/sections/{SECTION_ID}",
            json={
                "system_prompt": "Updated prompt.",
            },
        )

        delete_response = client.delete(
            f"/api/v1/normative/sections/{SECTION_ID}",
        )

    assert list_response.status_code == 200

    assert list_response.json()[0]["section_id"] == str(
        SECTION_ID,
    )

    assert create_response.status_code == 201

    assert create_response.json()["name"] == "ОВ"

    assert patch_response.status_code == 200

    assert patch_response.json()["system_prompt"] == "Updated prompt."

    assert manager.section_changes == {
        "system_prompt": "Updated prompt.",
    }

    assert delete_response.status_code == 200

    assert delete_response.json() == {
        "section_id": str(
            SECTION_ID,
        ),
        "deleted": True,
    }


def test_public_category_contract() -> None:
    """Публичный Gateway управляет category tree."""
    manager = FakeNormativeCatalogManager()

    with build_client(
        manager,
    ) as client:
        list_response = client.get(
            (f"/api/v1/normative/sections/{SECTION_ID}/categories"),
        )

        create_response = client.post(
            (f"/api/v1/normative/sections/{SECTION_ID}/categories"),
            json={
                "name": "Шкафы",
                "parent_id": None,
            },
        )

        patch_response = client.patch(
            f"/api/v1/normative/categories/{CATEGORY_ID}",
            json={
                "parent_id": None,
            },
        )

        delete_response = client.delete(
            f"/api/v1/normative/categories/{CATEGORY_ID}",
        )

    assert list_response.status_code == 200

    assert create_response.status_code == 201

    assert create_response.json()["name"] == "Шкафы"

    assert patch_response.status_code == 200

    assert manager.category_changes == {
        "parent_id": None,
    }

    assert delete_response.status_code == 200


def test_public_document_lifecycle_contract() -> None:
    """Gateway публикует upload, move, index, content и delete."""
    manager = FakeNormativeCatalogManager()

    with build_client(
        manager,
    ) as client:
        upload_response = client.post(
            (f"/api/v1/normative/sections/{SECTION_ID}/documents"),
            files={
                "file": (
                    "norm.pdf",
                    b"%PDF-test",
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
            (f"/api/v1/normative/documents/{DOCUMENT_ID}/index"),
        )

        move_response = client.patch(
            f"/api/v1/normative/documents/{DOCUMENT_ID}",
            json={
                "category_id": None,
            },
        )

        content_response = client.get(
            (f"/api/v1/normative/documents/{DOCUMENT_ID}/content"),
        )

        delete_response = client.delete(
            f"/api/v1/normative/documents/{DOCUMENT_ID}",
        )

    assert upload_response.status_code == 201

    assert manager.uploaded_content == b"%PDF-test"

    assert queue_response.status_code == 202

    assert queue_response.json()["index_status"] == "queued"

    assert move_response.status_code == 200

    assert move_response.json()["category_id"] is None

    assert content_response.status_code == 200

    assert content_response.content == b"%PDF-public-facade"

    assert content_response.headers["content-type"].startswith(
        "application/pdf",
    )

    assert content_response.headers["cache-control"] == "no-store"

    assert delete_response.status_code == 200


@pytest.mark.parametrize(
    (
        "error",
        "expected_status",
    ),
    (
        (
            NormativeCatalogNotFoundError(
                "not found",
            ),
            404,
        ),
        (
            NormativeCatalogConflictError(
                "conflict",
            ),
            409,
        ),
        (
            NormativeCatalogValidationError(
                "validation",
            ),
            422,
        ),
        (
            NormativeCatalogUnavailableError(
                "unavailable",
            ),
            503,
        ),
    ),
)
def test_public_facade_translates_application_errors(
    error: Exception,
    expected_status: int,
) -> None:
    """Gateway не раскрывает internal HTTP и сохраняет public semantics."""
    manager = FakeNormativeCatalogManager()

    manager.failure = error

    with build_client(
        manager,
    ) as client:
        response = client.get(
            f"/api/v1/normative/sections/{uuid4()}",
        )

    assert response.status_code == expected_status
