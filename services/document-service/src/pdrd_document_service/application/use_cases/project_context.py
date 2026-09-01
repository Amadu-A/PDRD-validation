# services/document-service/src/pdrd_document_service/application/use_cases/project_context.py

"""Use case извлечения диапазона Пояснительной записки."""

from dataclasses import dataclass

from pdrd_document_service.application.ports.pdf import PdfReader
from pdrd_document_service.application.use_cases.extract import (
    EmptyPdfError,
    PdfTooLargeError,
)
from pdrd_document_service.domain.project_context import (
    ExplanatoryNoteContext,
    parse_explanatory_note_range,
)


@dataclass(frozen=True, slots=True)
class ExtractPdfProjectContext:
    """Извлекает text-only диапазон ПЗ из исходного PDF."""

    reader: PdfReader

    max_upload_bytes: int
    max_context_pages: int

    def execute(
        self,
        *,
        content: bytes,
        enabled: bool,
        start_page: str | int | None,
        end_page: str | int | None,
    ) -> ExplanatoryNoteContext:
        """Проверяет диапазон и извлекает только текст ПЗ."""
        if not enabled:
            return ExplanatoryNoteContext.disabled()

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

        selected_pages = parse_explanatory_note_range(
            enabled=True,
            start_page=start_page,
            end_page=end_page,
            total_pages=total_pages,
            max_context_pages=self.max_context_pages,
        )

        pages = self.reader.extract_text(
            content,
            selected_pages=selected_pages,
        )

        return ExplanatoryNoteContext(
            enabled=True,
            start_page=selected_pages[0],
            end_page=selected_pages[-1],
            pages=pages,
        )
