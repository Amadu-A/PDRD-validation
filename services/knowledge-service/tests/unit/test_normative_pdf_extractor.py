# services/knowledge-service/tests/unit/test_normative_pdf_extractor.py

"""Unit tests PyMuPDF adapter нормативных документов."""

import pymupdf
import pytest
from pdrd_knowledge_service.application.ports.normative_pdf import (
    NormativePdfExtractionError,
)
from pdrd_knowledge_service.infrastructure.pdf.pymupdf import (
    PyMuPdfNormativePdfExtractor,
)


@pytest.mark.asyncio
async def test_pymupdf_extractor_returns_physical_pages() -> None:
    """Adapter сохраняет физическую нумерацию PDF-страниц."""
    document = pymupdf.open()

    first_page = document.new_page()

    first_page.insert_text(
        (
            72,
            72,
        ),
        "Normative first page.",
    )

    second_page = document.new_page()

    second_page.insert_text(
        (
            72,
            72,
        ),
        "Normative second page.",
    )

    content = document.tobytes()

    document.close()

    pages = await PyMuPdfNormativePdfExtractor().extract_pages(
        content=content,
    )

    assert (
        len(
            pages,
        )
        == 2
    )

    assert pages[0].page_number == 1

    assert "Normative first page." in pages[0].text

    assert pages[1].page_number == 2

    assert "Normative second page." in pages[1].text


@pytest.mark.asyncio
async def test_pymupdf_extractor_rejects_invalid_pdf() -> None:
    """Некорректные bytes преобразуются в application error."""
    with pytest.raises(
        NormativePdfExtractionError,
    ):
        await PyMuPdfNormativePdfExtractor().extract_pages(
            content=b"not a pdf",
        )
