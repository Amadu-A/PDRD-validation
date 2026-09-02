# services/knowledge-service/src/pdrd_knowledge_service/application/ports/normative_pdf.py

"""Application port извлечения текста нормативного PDF."""

from typing import Protocol

from pdrd_knowledge_service.domain.normative_indexing import (
    NormativeTextPage,
)


class NormativePdfExtractionError(RuntimeError):
    """Ошибка чтения или извлечения текста нормативного PDF."""


class NormativePdfExtractor(Protocol):
    """Контракт постраничного извлечения текста PDF."""

    async def extract_pages(
        self,
        *,
        content: bytes,
    ) -> tuple[
        NormativeTextPage,
        ...,
    ]:
        """Извлекает текст физических PDF-страниц."""
        ...
