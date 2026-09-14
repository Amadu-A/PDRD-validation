# services/knowledge-service/tests/unit/test_technical_assignment_scan_text.py

"""Regression tests scan-aware T text recovery."""

from typing import NoReturn

import pytest
from pdrd_knowledge_service.infrastructure.pdf.technical_assignment import (
    PyMuPdfTechnicalAssignmentProcessor,
)


def build_processor() -> PyMuPdfTechnicalAssignmentProcessor:
    """Создаёт processor с включённым OCR threshold."""
    return PyMuPdfTechnicalAssignmentProcessor(
        ocr_min_text_chars=80,
        ocr_executable="tesseract",
        ocr_language="rus+eng",
        ocr_page_segmentation_mode=3,
        ocr_timeout_seconds=60,
    )


def fail_ocr(
    image_bytes: bytes,
) -> NoReturn:
    """Падает, если OCR вызван неожиданно."""
    del image_bytes

    raise AssertionError(
        "OCR не должен был запускаться.",
    )


def test_native_text_skips_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Полноценный native text не отправляется в OCR."""
    processor = build_processor()

    monkeypatch.setattr(
        processor,
        "_extract_ocr_text",
        fail_ocr,
    )

    native_text = (
        "Настоящий цифровой текст технического задания. "
        "Проектом предусмотрено электроснабжение "
        "по двум независимым вводам с устройством АВР."
    )

    result = processor._recover_page_text(
        native_text=native_text,
        image_bytes=b"png",
        page_number=3,
    )

    assert result == native_text


def test_scan_page_uses_ocr_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image-only страница получает явный recovered text."""
    processor = build_processor()

    recovered = (
        "Техническое задание. "
        "Предусмотреть два независимых ввода "
        "электроснабжения с автоматическим "
        "вводом резерва. "
        "Решения выполнить согласно СП 256.1325800.2016."
    )

    monkeypatch.setattr(
        processor,
        "_extract_ocr_text",
        lambda image_bytes: recovered if image_bytes == b"scan" else "",
    )

    result = processor._recover_page_text(
        native_text="",
        image_bytes=b"scan",
        page_number=7,
    )

    assert result == recovered

    assert "СП 256.1325800.2016" in result


def test_short_native_text_is_replaced_only_by_better_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCR не ухудшает уже извлечённый короткий native text."""
    processor = build_processor()

    monkeypatch.setattr(
        processor,
        "_extract_ocr_text",
        lambda image_bytes: "ТЗ",
    )

    result = processor._recover_page_text(
        native_text="Раздел электроснабжения",
        image_bytes=b"scan",
        page_number=1,
    )

    assert result == "Раздел электроснабжения"
