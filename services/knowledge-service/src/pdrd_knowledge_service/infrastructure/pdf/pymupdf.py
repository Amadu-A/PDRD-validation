# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/pdf/pymupdf.py

"""PyMuPDF adapter извлечения текста нормативных PDF."""

import asyncio

import pymupdf

from pdrd_knowledge_service.application.ports.normative_pdf import (
    NormativePdfExtractionError,
)
from pdrd_knowledge_service.domain.normative_indexing import (
    NormativeTextPage,
)


class PyMuPdfNormativePdfExtractor:
    """Извлекает текст нормативного PDF через PyMuPDF."""

    async def extract_pages(
        self,
        *,
        content: bytes,
    ) -> tuple[
        NormativeTextPage,
        ...,
    ]:
        """Читает PDF вне asyncio event loop."""
        return await asyncio.to_thread(
            self._extract_sync,
            content,
        )

    @staticmethod
    def _extract_sync(
        content: bytes,
    ) -> tuple[
        NormativeTextPage,
        ...,
    ]:
        """Синхронно извлекает текст всех физических страниц."""
        if not content:
            raise NormativePdfExtractionError(
                "Нормативный PDF пуст.",
            )

        try:
            with pymupdf.open(
                stream=content,
                filetype="pdf",
            ) as document:
                pages = [
                    NormativeTextPage(
                        page_number=page_number,
                        text=page.get_text(
                            "text",
                            sort=True,
                        ),
                    )
                    for page_number, page in enumerate(
                        document,
                        start=1,
                    )
                ]

        except Exception as error:
            raise NormativePdfExtractionError(
                "Не удалось извлечь текст нормативного PDF.",
            ) from error

        if not pages:
            raise NormativePdfExtractionError(
                "Нормативный PDF не содержит страниц.",
            )

        return tuple(
            pages,
        )
