# services/knowledge-service/src/pdrd_knowledge_service/application/ports/technical_assignment_pdf.py

"""Application port multimodal подготовки страниц ТЗ."""

from typing import Protocol

from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    TechnicalAssignmentPage,
)


class TechnicalAssignmentPdfProcessingError(
    RuntimeError,
):
    """Ошибка обработки PDF технического задания."""


class TechnicalAssignmentPdfProcessor(
    Protocol,
):
    """Постраничная text+image подготовка ТЗ."""

    async def extract_pages(
        self,
        *,
        content: bytes,
        max_pages: int,
        render_dpi: int,
        max_image_pixels: int,
    ) -> tuple[
        TechnicalAssignmentPage,
        ...,
    ]:
        """Извлекает bounded multimodal representation страниц."""
        ...
