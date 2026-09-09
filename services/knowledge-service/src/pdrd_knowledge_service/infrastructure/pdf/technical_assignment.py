# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/pdf/technical_assignment.py

"""PyMuPDF processor multimodal страниц ТЗ."""

import asyncio
import math

import fitz

from pdrd_knowledge_service.application.ports.technical_assignment_pdf import (
    TechnicalAssignmentPdfProcessingError,
)
from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    TechnicalAssignmentPage,
)


class PyMuPdfTechnicalAssignmentProcessor:
    """Извлекает text и bounded PNG каждой страницы."""

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
        """Выполняет blocking PyMuPDF вне event loop."""
        return await asyncio.to_thread(
            self._extract_pages_sync,
            content,
            max_pages,
            render_dpi,
            max_image_pixels,
        )

    @staticmethod
    def _extract_pages_sync(
        content: bytes,
        max_pages: int,
        render_dpi: int,
        max_image_pixels: int,
    ) -> tuple[
        TechnicalAssignmentPage,
        ...,
    ]:
        """Рендерит страницы сразу внутри pixel budget."""
        if not content:
            raise TechnicalAssignmentPdfProcessingError(
                "PDF ТЗ пуст.",
            )

        try:
            document = fitz.open(
                stream=content,
                filetype="pdf",
            )

        except Exception as error:
            raise TechnicalAssignmentPdfProcessingError(
                "Не удалось открыть PDF технического задания.",
            ) from error

        try:
            if document.page_count < 1:
                raise TechnicalAssignmentPdfProcessingError(
                    "PDF ТЗ не содержит страниц.",
                )

            if document.page_count > max_pages:
                raise TechnicalAssignmentPdfProcessingError(
                    f"Количество страниц ТЗ превышает лимит {max_pages}.",
                )

            result: list[TechnicalAssignmentPage] = []

            requested_scale = render_dpi / 72.0

            for page_index in range(
                document.page_count,
            ):
                page = document.load_page(
                    page_index,
                )

                rectangle = page.rect

                estimated_width = max(
                    1,
                    int(rectangle.width * requested_scale),
                )

                estimated_height = max(
                    1,
                    int(rectangle.height * requested_scale),
                )

                scale = requested_scale

                estimated_pixels = estimated_width * estimated_height

                if estimated_pixels > max_image_pixels:
                    scale *= math.sqrt(max_image_pixels / estimated_pixels) * 0.99

                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(
                        scale,
                        scale,
                    ),
                    alpha=False,
                )

                pixel_count = pixmap.width * pixmap.height

                if pixel_count > max_image_pixels:
                    raise TechnicalAssignmentPdfProcessingError(
                        "Renderer превысил configured pixel budget страницы ТЗ.",
                    )

                text = (
                    page.get_text(
                        "text",
                    )
                    .replace(
                        "\x00",
                        " ",
                    )
                    .strip()
                )

                result.append(
                    TechnicalAssignmentPage(
                        page_number=page_index + 1,
                        text=text,
                        image_bytes=pixmap.tobytes(
                            "png",
                        ),
                        pixel_width=pixmap.width,
                        pixel_height=pixmap.height,
                    )
                )

            return tuple(
                result,
            )

        finally:
            document.close()
