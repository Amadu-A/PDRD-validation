# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/pdf/pymupdf.py

"""PyMuPDF adapter извлечения текста нормативных PDF."""

import asyncio
import math

import pymupdf

from pdrd_knowledge_service.application.ports.normative_pdf import (
    NormativePdfExtractionError,
)
from pdrd_knowledge_service.application.ports.technical_assignment_pdf import (
    TechnicalAssignmentPdfProcessingError,
)
from pdrd_knowledge_service.domain.normative_indexing import (
    NormativeTextPage,
)


class PyMuPdfNormativePdfExtractor:
    """Извлекает текст нормативного PDF через PyMuPDF."""

    def __init__(self, *, ocr_processor=None, max_ocr_pages=10):
        """OCR разрешён только atomic worker, порог ограничивает объём CPU-работы на документ."""
        self.ocr_processor = ocr_processor
        self.max_ocr_pages = max_ocr_pages

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

    def _extract_sync(
        self,
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
                pages, ocr_attempts = [], 0
                for page_number, page in enumerate(document, start=1):
                    text = page.get_text("text", sort=True)
                    method, error = "native_text", None
                    if not text.strip() and self.ocr_processor is not None:
                        if ocr_attempts >= self.max_ocr_pages:
                            error = "selective_ocr_page_limit"
                        elif page.get_images() or page.get_drawings():
                            ocr_attempts += 1
                            try:
                                # Тот же CPU OCR, без VLM; PNG создаётся только для пустой native-страницы.
                                scale = min(
                                    2.0,
                                    math.sqrt(
                                        4_000_000
                                        / max(1, page.rect.width * page.rect.height)
                                    ),
                                )
                                image = page.get_pixmap(
                                    matrix=pymupdf.Matrix(scale, scale), alpha=False
                                ).tobytes("png")
                                text = self.ocr_processor.recognize_image(image)
                                method = "ocr" if text.strip() else "native_text"
                                if not text.strip():
                                    error = "ocr_no_text"
                            except TechnicalAssignmentPdfProcessingError as failure:
                                error = str(failure)[:800]
                        else:
                            error = "empty_native_page"
                    pages.append(NormativeTextPage(page_number, text, method, error))

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
