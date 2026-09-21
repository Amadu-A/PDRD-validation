# services/document-service/src/pdrd_document_service/application/use_cases/annotate.py

"""Use case построения скачиваемого PDF с замечаниями."""

from dataclasses import dataclass

from pdrd_document_service.application.ports.pdf_annotation import (
    PdfAnnotationWriter,
    PdfFindingAnnotation,
    PdfTextReport,
)


class EmptyPdfAnnotationSourceError(ValueError):
    """Исходный PDF для annotation export пуст."""


class PdfAnnotationSourceTooLargeError(ValueError):
    """Исходный PDF превышает допустимый upload limit."""


@dataclass(frozen=True, slots=True)
class BuildAnnotatedPdf:
    """Добавляет annotations и текстовый отчёт к исходному PDF."""

    writer: PdfAnnotationWriter
    max_upload_bytes: int

    def execute(
        self,
        *,
        content: bytes,
        annotations: tuple[
            PdfFindingAnnotation,
            ...,
        ],
        report: PdfTextReport,
    ) -> bytes:
        """Проверяет bounded input и делегирует PDF infrastructure."""
        if not content:
            raise EmptyPdfAnnotationSourceError(
                "Исходный PDF для экспорта пуст.",
            )

        if len(content) > self.max_upload_bytes:
            raise PdfAnnotationSourceTooLargeError(
                "Размер исходного PDF превышает допустимый предел.",
            )

        return self.writer.build(
            content=content,
            annotations=annotations,
            report=report,
        )
