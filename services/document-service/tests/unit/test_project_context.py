# services/document-service/tests/unit/test_project_context.py

"""Unit tests Document Project Context extraction."""

import pytest
from pdrd_document_service.application.use_cases.project_context import (
    ExtractPdfProjectContext,
)
from pdrd_document_service.domain.project_context import (
    InvalidExplanatoryNoteRangeError,
    PdfTextPage,
)


class FakePdfReader:
    """Fake PDF adapter."""

    def get_page_count(
        self,
        content: bytes,
    ) -> int:
        """Возвращает десять страниц."""
        assert content

        return 10

    def extract(
        self,
        content: bytes,
        *,
        selected_pages: tuple[int, ...],
    ):
        """Не используется в этом test suite."""
        raise AssertionError(
            "extract must not be called",
        )

    def extract_text(
        self,
        content: bytes,
        *,
        selected_pages: tuple[int, ...],
    ) -> tuple[
        PdfTextPage,
        ...,
    ]:
        """Возвращает text-only pages."""
        assert content

        return tuple(
            PdfTextPage(
                number=page_number,
                text=(f"Текст ПЗ страницы {page_number}"),
            )
            for page_number in selected_pages
        )


def test_extract_project_context() -> None:
    """Извлекает диапазон без render."""
    use_case = ExtractPdfProjectContext(
        reader=FakePdfReader(),
        max_upload_bytes=1024,
        max_context_pages=20,
    )

    result = use_case.execute(
        content=b"pdf",
        enabled=True,
        start_page="2",
        end_page="4",
    )

    assert result.enabled is True

    assert result.start_page == 2
    assert result.end_page == 4

    assert tuple(page.number for page in result.pages) == (
        2,
        3,
        4,
    )


def test_project_context_range_checks_pdf_size() -> None:
    """Не принимает страницу за пределами PDF."""
    use_case = ExtractPdfProjectContext(
        reader=FakePdfReader(),
        max_upload_bytes=1024,
        max_context_pages=20,
    )

    with pytest.raises(
        InvalidExplanatoryNoteRangeError,
        match="за пределы",
    ):
        use_case.execute(
            content=b"pdf",
            enabled=True,
            start_page=8,
            end_page=12,
        )


def test_disabled_context_skips_pdf_reader() -> None:
    """Отключённый context не требует extraction."""
    use_case = ExtractPdfProjectContext(
        reader=FakePdfReader(),
        max_upload_bytes=1024,
        max_context_pages=20,
    )

    result = use_case.execute(
        content=b"",
        enabled=False,
        start_page=None,
        end_page=None,
    )

    assert result.enabled is False
    assert result.pages == ()
