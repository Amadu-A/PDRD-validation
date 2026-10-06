# services/document-service/src/pdrd_document_service/application/use_cases/extract.py

"""Use case подготовки PDF для дальнейшего инженерного анализа."""

from dataclasses import dataclass

from pdrd_document_service.application.ports.pdf import PdfReader
from pdrd_document_service.domain.pdf import (
    PdfDocument,
    PdfSelection,
    parse_page_spec,
)


class EmptyPdfError(ValueError):
    """Ошибка пустого PDF payload."""


class PdfTooLargeError(ValueError):
    """Ошибка превышения допустимого размера PDF."""


@dataclass(frozen=True, slots=True)
class ExtractPdfDocument:
    """Подготавливает выбранные страницы PDF."""

    reader: PdfReader
    max_upload_bytes: int
    max_analysis_pages: int

    def execute(
        self,
        *,
        content: bytes,
        page_spec: str | None,
    ) -> PdfDocument:
        """Проверяет выбор перед рендерингом и извлечением страниц."""
        selection = self.inspect(content=content, page_spec=page_spec)
        return self.reader.extract(content, selected_pages=selection.selected_pages)

    def inspect(self, *, content: bytes, page_spec: str | None) -> PdfSelection:
        """Проверяет размер и диапазон без подготовки изображений и вызова VLM."""
        if not content:
            raise EmptyPdfError(
                "Загруженный PDF пуст.",
            )

        if len(content) > self.max_upload_bytes:
            raise PdfTooLargeError(
                "Размер PDF превышает допустимый предел.",
            )

        total_pages = self.reader.get_page_count(
            content,
        )

        selected_pages = parse_page_spec(
            page_spec,
            total_pages=total_pages,
            max_selected_pages=self.max_analysis_pages,
        )

        return PdfSelection(total_pages=total_pages, selected_pages=selected_pages)
