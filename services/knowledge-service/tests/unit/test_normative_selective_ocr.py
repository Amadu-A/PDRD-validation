# services/knowledge-service/tests/unit/test_normative_selective_ocr.py

"""Native страницы не идут в OCR; сканы используют существующий CPU processor и лимит."""

from types import SimpleNamespace

import pymupdf
import pytest
from pdrd_knowledge_service.application.ports.technical_assignment_pdf import (
    TechnicalAssignmentPdfProcessingError,
)
from pdrd_knowledge_service.infrastructure.pdf.pymupdf import (
    PyMuPdfNormativePdfExtractor,
)


def mixed_pdf():
    """Создаёт документ с native-страницей, двумя графическими сканами и пустым листом."""
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 40), "Native text; do not OCR")
        for _ in range(2):
            page = pdf.new_page()
            page.draw_rect(pymupdf.Rect(30, 30, 100, 100), color=(0, 0, 0))
        pdf.new_page()
        return pdf.tobytes()


async def test_only_empty_native_pages_with_graphics_are_rendered_and_cpu_ocr_is_bounded():
    """Не вызывает VLM/embedding и не рендерит native текст ради OCR."""
    calls = []

    def recognize(image):
        """Заменяет только внешний Tesseract subprocess."""
        calls.append(image)
        return "Распознанный нормативный текст."

    pages = await PyMuPdfNormativePdfExtractor(
        ocr_processor=SimpleNamespace(recognize_image=recognize), max_ocr_pages=1
    ).extract_pages(content=mixed_pdf())
    assert len(calls) == 1 and len(pages) == 4
    assert pages[0].extraction_method == "native_text"
    assert pages[1].extraction_method == "ocr" and pages[1].text
    assert pages[2].extraction_error == "selective_ocr_page_limit"
    assert not pages[3].text


async def test_expected_ocr_failure_retains_every_page_and_error():
    """Ошибка Tesseract не теряет исходные страницы и не становится успешным извлечением."""

    def fail(image):
        """Имитирует отсутствие установленного языкового пакета."""
        raise TechnicalAssignmentPdfProcessingError("OCR language unavailable")

    pages = await PyMuPdfNormativePdfExtractor(
        ocr_processor=SimpleNamespace(recognize_image=fail)
    ).extract_pages(content=mixed_pdf())
    assert len(pages) == 4 and pages[0].text
    assert pages[1].extraction_error == "OCR language unavailable" and not pages[1].text
    assert pages[2].extraction_error == "OCR language unavailable" and not pages[2].text


async def test_programming_error_is_propagated_instead_of_partial_empty_success():
    """Непредвиденный дефект отличается от ожидаемого отказа OCR."""

    def broken(image):
        """Неожиданная программная ошибка сохраняется как cause отказа PDF extractor."""
        raise KeyError("implementation_bug")

    with pytest.raises(RuntimeError) as failure:
        await PyMuPdfNormativePdfExtractor(
            ocr_processor=SimpleNamespace(recognize_image=broken)
        ).extract_pages(content=mixed_pdf())
    assert isinstance(failure.value.__cause__, KeyError)
