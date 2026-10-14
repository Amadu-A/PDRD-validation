# services/document-service/tests/unit/test_equipment_document.py

"""Проверяет текстовое извлечение EQ PDF/HTML без активного содержимого."""

import fitz
import pytest
from pdrd_document_service.application.use_cases.equipment_document import (
    ExtractEquipmentDocument,
)
from pdrd_document_service.infrastructure.equipment_document import (
    PyMuPdfEquipmentDocumentReader,
)


def _pdf() -> bytes:
    """Создаёт небольшой текстовый PDF с двумя страницами."""
    document = fitz.open()
    document.new_page().insert_text((72, 72), "MEAN WELL DRC-100B")
    document.new_page().insert_text((72, 72), "Output 27.6 V DC")
    content = document.tobytes()
    document.close()
    return content


def test_pdf_text_pages_preserve_physical_page_numbers() -> None:
    """Доказательство остаётся привязано к конкретной странице."""
    result = ExtractEquipmentDocument(
        PyMuPdfEquipmentDocumentReader(),
        max_pages=1,
    ).execute(_pdf(), "application/pdf")
    assert result.total_pages == 2
    assert [page.page for page in result.pages] == [1]
    assert "DRC-100B" in result.pages[0].text


def test_html_discards_scripts_and_styles() -> None:
    """Активный HTML рассматривается только как недоверенные данные."""
    content = (
        b"<html><head><script>BAD_INSTRUCTION</script>"
        b"<style>.hidden{display:none}</style></head>"
        b"<body><h1>DRC-100B</h1><p>27.6 V DC</p></body></html>"
    )
    result = ExtractEquipmentDocument(
        PyMuPdfEquipmentDocumentReader(),
    ).execute(content, "text/html")
    assert "DRC-100B" in result.pages[0].text
    assert "BAD_INSTRUCTION" not in result.pages[0].text
    assert "display:none" not in result.pages[0].text


def test_unsupported_or_oversized_document_is_rejected() -> None:
    """Извлечение соблюдает лимит байтов и формата."""
    use_case = ExtractEquipmentDocument(
        PyMuPdfEquipmentDocumentReader(),
        max_bytes=4,
    )
    with pytest.raises(ValueError):
        use_case.execute(b"too long", "text/html")
    with pytest.raises(ValueError):
        use_case.execute(b"abc", "application/zip")
