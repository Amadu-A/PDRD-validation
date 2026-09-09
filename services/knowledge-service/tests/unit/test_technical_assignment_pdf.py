# services/knowledge-service/tests/unit/test_technical_assignment_pdf.py

"""Unit tests bounded PyMuPDF rendering ТЗ."""

import fitz
from pdrd_knowledge_service.infrastructure.pdf.technical_assignment import (
    PyMuPdfTechnicalAssignmentProcessor,
)


def build_large_page_pdf() -> bytes:
    """Создаёт одну большую PDF-страницу."""
    document = fitz.open()

    page = document.new_page(
        width=3370,
        height=2384,
    )

    # Стандартный встроенный шрифт PyMuPDF в insert_text
    # не гарантирует поддержку кириллицы.
    # Этот fixture проверяет bounded rendering, а не Unicode font embedding.
    page.insert_text(
        (
            100,
            100,
        ),
        "Technical requirement IP54",
    )

    content = document.tobytes()

    document.close()

    return content


async def test_large_page_is_rendered_inside_pixel_budget() -> None:
    """A0-like page не создаёт unlimited bitmap."""
    processor = PyMuPdfTechnicalAssignmentProcessor()

    max_pixels = 1_843_200

    pages = await processor.extract_pages(
        content=build_large_page_pdf(),
        max_pages=10,
        render_dpi=150,
        max_image_pixels=max_pixels,
    )

    assert (
        len(
            pages,
        )
        == 1
    )

    page = pages[0]

    assert page.pixel_width * page.pixel_height <= max_pixels

    assert "IP54" in page.text
