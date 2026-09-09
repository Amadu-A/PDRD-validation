# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/pdf/technical_assignment.py

"""PyMuPDF processor multimodal страниц ТЗ."""

import asyncio
import logging
import math
import subprocess

import fitz

from pdrd_knowledge_service.application.ports.technical_assignment_pdf import (
    TechnicalAssignmentPdfProcessingError,
)
from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    TechnicalAssignmentPage,
)

logger = logging.getLogger(
    __name__,
)

_OCR_DIAGNOSTIC_LIMIT = 800


class PyMuPdfTechnicalAssignmentProcessor:
    """Извлекает native/OCR text и bounded PNG каждой страницы."""

    def __init__(
        self,
        *,
        ocr_min_text_chars: int = 0,
        ocr_executable: str = "tesseract",
        ocr_language: str = "rus+eng",
        ocr_page_segmentation_mode: int = 3,
        ocr_timeout_seconds: float = 60.0,
    ) -> None:
        """Сохраняет scan-aware OCR settings."""
        self._ocr_min_text_chars = ocr_min_text_chars

        self._ocr_executable = ocr_executable

        self._ocr_language = ocr_language

        self._ocr_page_segmentation_mode = ocr_page_segmentation_mode

        self._ocr_timeout_seconds = ocr_timeout_seconds

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
        """Выполняет blocking PyMuPDF/OCR вне event loop."""
        return await asyncio.to_thread(
            self._extract_pages_sync,
            content,
            max_pages,
            render_dpi,
            max_image_pixels,
        )

    def _extract_pages_sync(
        self,
        content: bytes,
        max_pages: int,
        render_dpi: int,
        max_image_pixels: int,
    ) -> tuple[
        TechnicalAssignmentPage,
        ...,
    ]:
        """Рендерит страницы и восстанавливает текст сканов."""
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

                image_bytes = pixmap.tobytes(
                    "png",
                )

                native_text = (
                    page.get_text(
                        "text",
                    )
                    .replace(
                        "\x00",
                        " ",
                    )
                    .strip()
                )

                text = self._recover_page_text(
                    native_text=native_text,
                    image_bytes=image_bytes,
                    page_number=(page_index + 1),
                )

                result.append(
                    TechnicalAssignmentPage(
                        page_number=(page_index + 1),
                        text=text,
                        image_bytes=image_bytes,
                        pixel_width=(pixmap.width),
                        pixel_height=(pixmap.height),
                    )
                )

            return tuple(
                result,
            )

        finally:
            document.close()

    def _recover_page_text(
        self,
        *,
        native_text: str,
        image_bytes: bytes,
        page_number: int,
    ) -> str:
        """Использует OCR только при недостаточном native text."""
        normalized_native = self._normalize_text(
            native_text,
        )

        if (
            self._ocr_min_text_chars <= 0
            or len(
                normalized_native,
            )
            >= self._ocr_min_text_chars
        ):
            return normalized_native

        ocr_text = self._normalize_text(
            self._extract_ocr_text(
                image_bytes,
            )
        )

        if len(
            ocr_text,
        ) > len(
            normalized_native,
        ):
            logger.info(
                (
                    "technical_assignment_ocr_applied "
                    "page=%s native_chars=%s ocr_chars=%s"
                ),
                page_number,
                len(
                    normalized_native,
                ),
                len(
                    ocr_text,
                ),
            )

            return ocr_text

        logger.info(
            ("technical_assignment_ocr_no_gain page=%s native_chars=%s ocr_chars=%s"),
            page_number,
            len(
                normalized_native,
            ),
            len(
                ocr_text,
            ),
        )

        return normalized_native

    def _extract_ocr_text(
        self,
        image_bytes: bytes,
    ) -> str:
        """Распознаёт PNG через bounded Tesseract stdin/stdout."""
        command = [
            self._ocr_executable,
            "stdin",
            "stdout",
            "-l",
            self._ocr_language,
            "--psm",
            str(
                self._ocr_page_segmentation_mode,
            ),
        ]

        try:
            result = subprocess.run(
                command,
                input=image_bytes,
                capture_output=True,
                check=False,
                timeout=(self._ocr_timeout_seconds),
            )

        except FileNotFoundError as error:
            raise TechnicalAssignmentPdfProcessingError(
                "Tesseract executable не найден.",
            ) from error

        except subprocess.TimeoutExpired as error:
            raise TechnicalAssignmentPdfProcessingError(
                "Превышено время OCR страницы ТЗ.",
            ) from error

        except OSError as error:
            raise TechnicalAssignmentPdfProcessingError(
                "Не удалось запустить OCR страницы ТЗ.",
            ) from error

        if result.returncode != 0:
            diagnostic = result.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()[:_OCR_DIAGNOSTIC_LIMIT]

            logger.warning(
                "technical_assignment_ocr_failed returncode=%s stderr=%r",
                result.returncode,
                diagnostic,
            )

            raise TechnicalAssignmentPdfProcessingError(
                f"Tesseract завершил OCR страницы ТЗ с кодом {result.returncode}.",
            )

        return result.stdout.decode(
            "utf-8",
            errors="replace",
        )

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:
        """Убирает нулевые символы и лишние пробелы строк."""
        return "\n".join(
            line.strip()
            for line in (
                value.replace(
                    "\x00",
                    " ",
                )
                .replace(
                    "\r\n",
                    "\n",
                )
                .replace(
                    "\r",
                    "\n",
                )
                .split(
                    "\n",
                )
            )
            if line.strip()
        ).strip()
