# services/document-service/src/pdrd_document_service/core/container.py

"""Composition root Document Service."""

from dataclasses import dataclass

from pdrd_document_service.application.use_cases.extract import (
    ExtractPdfDocument,
)
from pdrd_document_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_document_service.infrastructure.pdf.pymupdf import (
    PyMuPdfReader,
)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит runtime dependencies Document Service."""

    settings: Settings
    extract_pdf: ExtractPdfDocument


def build_container() -> ApplicationContainer:
    """Собирает concrete dependencies сервиса."""
    settings = get_settings()

    reader = PyMuPdfReader(
        render_max_side=(settings.pdf.render_max_side),
        text_limit=settings.pdf.text_limit,
    )

    extract_pdf = ExtractPdfDocument(
        reader=reader,
        max_upload_bytes=(settings.pdf.max_upload_bytes),
        max_analysis_pages=(settings.pdf.max_analysis_pages),
    )

    return ApplicationContainer(
        settings=settings,
        extract_pdf=extract_pdf,
    )
