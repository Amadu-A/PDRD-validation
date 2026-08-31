# services/document-service/tests/integration/test_pymupdf_reader.py

"""Integration-тест PyMuPDF adapter."""

import fitz
from pdrd_document_service.domain.pdf import PdfPageType
from pdrd_document_service.infrastructure.pdf.pymupdf import (
    PyMuPdfReader,
)


def build_pdf() -> bytes:
    """Создаёт небольшой PDF полностью в памяти."""
    document = fitz.open()

    first_page = document.new_page()

    first_page.insert_text(
        (
            72,
            72,
        ),
        "Project documentation",
    )

    second_page = document.new_page()

    second_page.insert_text(
        (
            72,
            72,
        ),
        "Test drawing page",
    )

    content = document.tobytes()

    document.close()

    return content


def test_reader_extracts_selected_page_and_png() -> None:
    """Проверяет text extraction и render выбранной страницы."""
    reader = PyMuPdfReader(
        render_max_side=1200,
        text_limit=12000,
    )

    content = build_pdf()

    assert (
        reader.get_page_count(
            content,
        )
        == 2
    )

    result = reader.extract(
        content,
        selected_pages=(2,),
    )

    assert result.total_pages == 2
    assert result.selected_page_numbers == (2,)

    page = result.pages[0]

    assert page.number == 2
    assert page.page_type is PdfPageType.UNKNOWN
    assert "Test drawing page" in page.text

    assert page.rendered_png.startswith(
        b"\x89PNG\r\n\x1a\n",
    )

    assert page.width_points > 0
    assert page.height_points > 0
