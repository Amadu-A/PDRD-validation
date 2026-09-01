# services/document-service/src/pdrd_document_service/application/ports/pdf.py

"""Application port для чтения и рендеринга PDF."""

from typing import Protocol

from pdrd_document_service.domain.pdf import PdfDocument
from pdrd_document_service.domain.project_context import PdfTextPage


class PdfProcessingError(RuntimeError):
    """Ошибка инфраструктурного чтения или рендеринга PDF."""


class PdfReader(Protocol):
    """Контракт PDF infrastructure adapter."""

    def get_page_count(
        self,
        content: bytes,
    ) -> int:
        """Возвращает количество физических PDF-страниц."""
        ...

    def extract(
        self,
        content: bytes,
        *,
        selected_pages: tuple[int, ...],
    ) -> PdfDocument:
        """Извлекает и рендерит выбранные страницы."""
        ...

    def extract_text(
        self,
        content: bytes,
        *,
        selected_pages: tuple[int, ...],
    ) -> tuple[PdfTextPage, ...]:
        """Извлекает текст без рендера выбранных страниц."""
        ...
