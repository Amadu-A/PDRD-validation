# services/document-service/tests/unit/test_pdf_domain.py

"""Unit-тесты PDF domain rules."""

import pytest
from pdrd_document_service.domain.pdf import (
    InvalidPageSelectionError,
    PdfPageType,
    classify_page,
    parse_page_spec,
)


def test_empty_page_spec_selects_all_pages() -> None:
    """Проверяет выбор всего PDF при пустом page spec."""
    assert parse_page_spec(
        None,
        total_pages=4,
        max_selected_pages=10,
    ) == (
        1,
        2,
        3,
        4,
    )


def test_page_spec_supports_ranges_and_duplicates() -> None:
    """Проверяет ranges, sorting и deduplication."""
    assert parse_page_spec(
        "5, 2-4, 3",
        total_pages=10,
        max_selected_pages=10,
    ) == (
        2,
        3,
        4,
        5,
    )


def test_page_spec_rejects_page_outside_document() -> None:
    """Проверяет страницу за пределами PDF."""
    with pytest.raises(
        InvalidPageSelectionError,
    ):
        parse_page_spec(
            "1,4",
            total_pages=3,
            max_selected_pages=10,
        )


def test_page_spec_rejects_too_many_pages() -> None:
    """Проверяет ограничение количества анализируемых страниц."""
    with pytest.raises(
        InvalidPageSelectionError,
    ):
        parse_page_spec(
            None,
            total_pages=3,
            max_selected_pages=2,
        )


@pytest.mark.parametrize(
    (
        "text",
        "page_number",
        "expected",
    ),
    [
        (
            "Рабочая документация",
            1,
            PdfPageType.TITLE,
        ),
        (
            "Спецификация оборудования",
            2,
            PdfPageType.SPECIFICATION,
        ),
        (
            "Общие указания",
            4,
            PdfPageType.GENERAL_NOTES,
        ),
        (
            "Схема подключения оборудования",
            5,
            PdfPageType.SCHEME,
        ),
        (
            "Неизвестный лист",
            6,
            PdfPageType.UNKNOWN,
        ),
    ],
)
def test_classify_page(
    text: str,
    page_number: int,
    expected: PdfPageType,
) -> None:
    """Проверяет deterministic классификацию PDF-листов."""
    assert (
        classify_page(
            text,
            page_number=page_number,
        )
        is expected
    )
