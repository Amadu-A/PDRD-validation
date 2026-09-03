# services/api-gateway/tests/unit/test_normative_catalog_management.py

"""Unit tests Gateway adapter managed normative catalog."""

from uuid import UUID

import httpx
import pytest
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogConflictError,
)
from pdrd_api_gateway.core.settings import (
    KnowledgeServiceSettings,
)
from pdrd_api_gateway.infrastructure.knowledge.normative_catalog_management import (
    HttpNormativeCatalogManager,
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

CREATED_AT = "2026-09-03T08:00:00Z"

UPDATED_AT = "2026-09-03T08:01:00Z"


def build_manager(
    handler: httpx.AsyncBaseTransport,
) -> HttpNormativeCatalogManager:
    """Создаёт adapter с test transport."""
    return HttpNormativeCatalogManager(
        settings=KnowledgeServiceSettings(
            base_url="http://knowledge.test:8401",
        ),
        transport=handler,
    )


def section_payload() -> dict[
    str,
    object,
]:
    """Возвращает valid section payload."""
    return {
        "section_id": str(
            SECTION_ID,
        ),
        "name": "ЭОМ",
        "system_prompt": "System prompt.",
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }


def document_payload(
    *,
    index_status: str = "uploaded",
    ready_for_analysis: bool = False,
) -> dict[
    str,
    object,
]:
    """Возвращает valid document payload."""
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
        "original_name": "norm.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 123,
        "index_status": index_status,
        "index_error": None,
        "indexed_at": (UPDATED_AT if ready_for_analysis else None),
        "ready_for_analysis": ready_for_analysis,
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }


@pytest.mark.asyncio
async def test_manager_lists_sections() -> None:
    """Adapter разбирает section list Knowledge Service."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "GET"

        assert request.url.path == ("/internal/v1/normative/sections")

        return httpx.Response(
            200,
            json=[
                section_payload(),
            ],
        )

    manager = build_manager(
        httpx.MockTransport(
            handler,
        )
    )

    sections = await manager.list_sections()

    assert (
        len(
            sections,
        )
        == 1
    )

    assert sections[0].section_id == SECTION_ID

    assert sections[0].system_prompt == "System prompt."


@pytest.mark.asyncio
async def test_manager_forwards_document_upload() -> None:
    """Adapter пересылает file и category как multipart."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "POST"

        assert request.url.path == (
            f"/internal/v1/normative/sections/{SECTION_ID}/documents"
        )

        body = await request.aread()

        content_type = request.headers["content-type"]

        assert "multipart/form-data" in content_type

        assert b"norm.pdf" in body

        assert b"pdf-content" in body

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
        original_name="norm.pdf",
        content=b"pdf-content",
        content_type="application/pdf",
    )

    assert document.document_id == DOCUMENT_ID

    assert document.category_id == CATEGORY_ID


@pytest.mark.asyncio
async def test_manager_maps_upstream_conflict() -> None:
    """HTTP 409 Knowledge превращается в application conflict."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        del request

        return httpx.Response(
            409,
            json={
                "detail": "Document is indexing.",
            },
        )

    manager = build_manager(
        httpx.MockTransport(
            handler,
        )
    )

    with pytest.raises(
        NormativeCatalogConflictError,
        match="Document is indexing",
    ):
        await manager.delete_document(
            document_id=DOCUMENT_ID,
        )


@pytest.mark.asyncio
async def test_manager_returns_document_content() -> None:
    """Adapter возвращает бинарный PDF без JSON decoding."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.url.path == (
            f"/internal/v1/normative/documents/{DOCUMENT_ID}/content"
        )

        return httpx.Response(
            200,
            content=b"%PDF-facade-test",
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

    assert result.content == b"%PDF-facade-test"

    assert result.mime_type == "application/pdf"
