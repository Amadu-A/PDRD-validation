# services/equipment-search-service/tests/unit/test_inspect_document.py

"""Проверяет принадлежность текстового PDF конкретному оборудованию."""

import httpx
import pytest
from pdrd_equipment_search_service.domain.equipment import (
    DownloadedDocument,
    EquipmentIdentity,
)
from pdrd_equipment_search_service.infrastructure.inspect_document import (
    DocumentServiceInspector,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "applicable"),
    [
        ("MEAN WELL DRC-100B Rev 1.2", True),
        ("MEAN WELL DRC-100C Rev 1.2", False),
        ("MEAN WELL DRC-100B-A Rev 1.2", False),
        ("Other Brand DRC-100B", False),
    ],
)
async def test_document_must_name_manufacturer_and_exact_model(
    text: str,
    applicable: bool,
) -> None:
    """Похожая модель либо другой производитель не становится evidence."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/internal/v1/equipment-documents/extract"
        return httpx.Response(
            200,
            json={
                "media_type": "application/pdf",
                "total_pages": 1,
                "pages": [{"page": 1, "text": text}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DocumentServiceInspector(
            client, "http://document-service:8301"
        ).inspect(
            EquipmentIdentity("MEAN WELL", "DRC-100B"),
            DownloadedDocument(
                b"%PDF-1.7",
                "https://meanwell.com/manual.pdf",
                "application/pdf",
            ),
        )
    assert result.applicable is applicable
    if applicable:
        assert result.revision == "1.2"
        assert result.pages == ((1, text),)


@pytest.mark.asyncio
async def test_variant_must_be_visible_in_document() -> None:
    """Исполнение не достраивается по одной базовой модели."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"pages": [{"page": 1, "text": "CHINT NXB-63"}]},
            )
        ),
    ) as client:
        result = await DocumentServiceInspector(
            client,
            "http://document-service:8301",
        ).inspect(
            EquipmentIdentity("CHINT", "NXB-63", variant="C16"),
            DownloadedDocument(
                b"%PDF-1.7",
                "https://chintglobal.com/nxb63.pdf",
                "application/pdf",
            ),
        )
    assert result.applicable is False


@pytest.mark.asyncio
async def test_scanned_pdf_requires_targeted_visual_review() -> None:
    """Пустой текст скана не принимается как доказательство модели."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"pages": [{"page": 1, "text": ""}]}
            )
        )
    ) as client:
        result = await DocumentServiceInspector(
            client, "http://document-service:8301"
        ).inspect(
            EquipmentIdentity("CHINT", "NXB-63"),
            DownloadedDocument(
                b"%PDF-1.7",
                "https://www.chintglobal.com/nxb.pdf",
                "application/pdf",
            ),
        )
    assert result.applicable is False
    assert result.requires_vision is True


@pytest.mark.asyncio
async def test_explicit_publication_date_is_normalized() -> None:
    """Дата публикации берётся только из подписи и проверяется календарём."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "pages": [
                        {
                            "page": 1,
                            "text": "MEAN WELL DRC-100B Rev B publication date: 08.10.2025",
                        }
                    ]
                },
            )
        )
    ) as client:
        result = await DocumentServiceInspector(
            client, "http://document-service:8301"
        ).inspect(
            EquipmentIdentity("MEAN WELL", "DRC-100B"),
            DownloadedDocument(
                b"%PDF-1.7",
                "https://www.meanwell.com/drc.pdf",
                "application/pdf",
            ),
        )
    assert result.publication_date == "2025-10-08"
