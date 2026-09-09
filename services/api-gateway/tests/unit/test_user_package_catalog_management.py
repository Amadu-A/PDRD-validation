# services/api-gateway/tests/unit/test_user_package_catalog_management.py

"""Unit tests Gateway adapter пользовательских пакетов."""

from uuid import UUID

import httpx
import pytest
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogNotFoundError,
)
from pdrd_api_gateway.core.settings import (
    KnowledgeServiceSettings,
)
from pdrd_api_gateway.infrastructure.knowledge.user_package_catalog import (
    HttpUserPackageCatalogManager,
)

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

CATEGORY_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

DOCUMENT_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

CREATED_AT = "2026-09-04T06:00:00Z"

UPDATED_AT = "2026-09-04T06:01:00Z"


def build_manager(
    transport: httpx.AsyncBaseTransport,
) -> HttpUserPackageCatalogManager:
    """Создаёт adapter с test transport."""
    return HttpUserPackageCatalogManager(
        settings=KnowledgeServiceSettings(
            base_url="http://knowledge.test:8401",
        ),
        transport=transport,
    )


def category_payload(
    *,
    area: str = "user_package",
) -> dict[
    str,
    object,
]:
    """Возвращает category payload."""
    return {
        "category_id": str(
            CATEGORY_ID,
        ),
        "section_id": str(
            SECTION_ID,
        ),
        "parent_id": None,
        "name": "Пакет заказчика",
        "area": area,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }


def document_payload(
    *,
    area: str = "user_package",
) -> dict[
    str,
    object,
]:
    """Возвращает document payload."""
    return {
        "document_id": str(
            DOCUMENT_ID,
        ),
        "section_id": str(
            SECTION_ID,
        ),
        "category_id": str(
            CATEGORY_ID,
        ),
        "original_name": "package.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 100,
        "area": area,
        "index_status": "uploaded",
        "index_error": None,
        "indexed_at": None,
        "ready_for_analysis": False,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }


@pytest.mark.asyncio
async def test_lists_only_user_package_area() -> None:
    """Adapter передаёт area=user_package в internal GET."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "GET"

        assert request.url.path == (
            f"/internal/v1/normative/sections/{SECTION_ID}/categories"
        )

        assert request.url.params["area"] == "user_package"

        return httpx.Response(
            200,
            json=[
                category_payload(),
            ],
        )

    manager = build_manager(
        httpx.MockTransport(
            handler,
        )
    )

    result = await manager.list_categories(
        section_id=SECTION_ID,
    )

    assert (
        len(
            result,
        )
        == 1
    )

    assert result[0].category_id == CATEGORY_ID


@pytest.mark.asyncio
async def test_upload_forwards_user_package_area() -> None:
    """Multipart upload содержит area=user_package."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "POST"

        assert request.url.path == (
            f"/internal/v1/normative/sections/{SECTION_ID}/documents"
        )

        body = await request.aread()

        assert b"user_package" in body

        assert b"package.pdf" in body

        assert (
            str(
                CATEGORY_ID,
            ).encode()
            in body
        )

        return httpx.Response(
            201,
            json=document_payload(),
        )

    manager = build_manager(
        httpx.MockTransport(
            handler,
        )
    )

    document = await manager.upload_document(
        section_id=SECTION_ID,
        category_id=CATEGORY_ID,
        original_name="package.pdf",
        content=b"%PDF-package",
        content_type="application/pdf",
    )

    assert document.document_id == DOCUMENT_ID

    assert document.category_id == CATEGORY_ID


@pytest.mark.asyncio
async def test_normative_document_is_hidden_from_package_api() -> None:
    """Normative UUID нельзя использовать через user-package facade."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.url.path == (f"/internal/v1/normative/documents/{DOCUMENT_ID}")

        return httpx.Response(
            200,
            json=document_payload(
                area="normative",
            ),
        )

    manager = build_manager(
        httpx.MockTransport(
            handler,
        )
    )

    with pytest.raises(
        NormativeCatalogNotFoundError,
        match="пользовательского пакета",
    ):
        await manager.get_document(
            document_id=DOCUMENT_ID,
        )


@pytest.mark.asyncio
async def test_returns_package_document_content() -> None:
    """Adapter сначала проверяет area, затем возвращает PDF."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.path == (f"/internal/v1/normative/documents/{DOCUMENT_ID}"):
            return httpx.Response(
                200,
                json=document_payload(),
            )

        assert request.url.path == (
            f"/internal/v1/normative/documents/{DOCUMENT_ID}/content"
        )

        return httpx.Response(
            200,
            content=b"%PDF-user-package",
            headers={
                "Content-Type": "application/pdf",
            },
        )

    manager = build_manager(
        httpx.MockTransport(
            handler,
        )
    )

    result = await manager.get_document_content(
        document_id=DOCUMENT_ID,
    )

    assert result.content == b"%PDF-user-package"

    assert result.mime_type == "application/pdf"
