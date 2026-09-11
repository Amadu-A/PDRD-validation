# services/document-service/src/pdrd_document_service/infrastructure/pdf/pymupdf.py

"""PyMuPDF adapter для чтения и рендеринга PDF."""

import math

import fitz

from pdrd_document_service.application.ports.pdf import (
    PdfProcessingError,
)
from pdrd_document_service.domain.pdf import (
    PdfDocument,
    PdfNormalizedBoundingBox,
    PdfPage,
    PdfTextWord,
    classify_page,
)
from pdrd_document_service.domain.project_context import (
    PdfTextPage,
)


class PyMuPdfReader:
    """Читает PDF через PyMuPDF."""

    def __init__(
        self,
        *,
        render_max_side: int,
        text_limit: int,
    ) -> None:
        """Сохраняет ограничения extraction."""
        self._render_max_side = render_max_side
        self._text_limit = text_limit

    def get_page_count(
        self,
        content: bytes,
    ) -> int:
        """Возвращает количество физических страниц."""
        try:
            with fitz.open(
                stream=content,
                filetype="pdf",
            ) as document:
                return document.page_count

        except Exception as error:
            raise PdfProcessingError(
                "Не удалось открыть PDF.",
            ) from error

    def extract(
        self,
        content: bytes,
        *,
        selected_pages: tuple[
            int,
            ...,
        ],
    ) -> PdfDocument:
        """Извлекает текст, text geometry и PNG выбранных страниц."""
        try:
            with fitz.open(
                stream=content,
                filetype="pdf",
            ) as document:
                pages = tuple(
                    self._extract_page(
                        document[page_number - 1],
                        page_number=page_number,
                    )
                    for page_number in selected_pages
                )

                return PdfDocument(
                    total_pages=document.page_count,
                    pages=pages,
                )

        except PdfProcessingError:
            raise

        except Exception as error:
            raise PdfProcessingError(
                "Ошибка при обработке PDF.",
            ) from error

    def extract_text(
        self,
        content: bytes,
        *,
        selected_pages: tuple[
            int,
            ...,
        ],
    ) -> tuple[
        PdfTextPage,
        ...,
    ]:
        """Извлекает text-only страницы для контекста ПЗ."""
        try:
            with fitz.open(
                stream=content,
                filetype="pdf",
            ) as document:
                return tuple(
                    PdfTextPage(
                        number=page_number,
                        text=(
                            document[page_number - 1].get_text(
                                "text",
                                sort=True,
                            )[: self._text_limit]
                        ).strip(),
                    )
                    for page_number in selected_pages
                )

        except Exception as error:
            raise PdfProcessingError(
                "Не удалось извлечь текст страниц ПЗ.",
            ) from error

    def _extract_page(
        self,
        page: fitz.Page,
        *,
        page_number: int,
    ) -> PdfPage:
        text = (
            page.get_text(
                "text",
                sort=True,
            )[: self._text_limit]
        ).strip()

        text_words = self._extract_text_words(
            page,
        )

        rendered_png = self._render_page(
            page,
        )

        return PdfPage(
            number=page_number,
            page_type=classify_page(
                text,
                page_number=page_number,
            ),
            text=text,
            width_points=round(
                float(
                    page.rect.width,
                ),
                3,
            ),
            height_points=round(
                float(
                    page.rect.height,
                ),
                3,
            ),
            rendered_png=rendered_png,
            text_words=text_words,
        )

    def _extract_text_words(
        self,
        page: fitz.Page,
    ) -> tuple[
        PdfTextWord,
        ...,
    ]:
        """Извлекает слова PDF вместе с нормализованными bbox."""
        page_width = float(
            page.rect.width,
        )

        page_height = float(
            page.rect.height,
        )

        if page_width <= 0 or page_height <= 0:
            return ()

        raw_words = page.get_text(
            "words",
            sort=True,
        )

        result: list[PdfTextWord] = []

        for raw_word in raw_words:
            if (
                len(
                    raw_word,
                )
                < 8
            ):
                continue

            text = str(raw_word[4]).strip()

            if not text:
                continue

            try:
                raw_rect = fitz.Rect(
                    float(raw_word[0]),
                    float(raw_word[1]),
                    float(raw_word[2]),
                    float(raw_word[3]),
                )

                visual_rect = (
                    raw_rect * page.rotation_matrix if page.rotation else raw_rect
                )

                bbox = self._normalize_bbox(
                    rect=visual_rect,
                    page_width=page_width,
                    page_height=page_height,
                )

                result.append(
                    PdfTextWord(
                        text=text,
                        bbox=bbox,
                        block_no=int(raw_word[5]),
                        line_no=int(raw_word[6]),
                        word_no=int(raw_word[7]),
                    )
                )

            except (
                TypeError,
                ValueError,
                OverflowError,
            ):
                continue

        return tuple(
            result,
        )

    @staticmethod
    def _normalize_bbox(
        *,
        rect: fitz.Rect,
        page_width: float,
        page_height: float,
    ) -> PdfNormalizedBoundingBox:
        """Переводит PDF points в общую систему координат 0..1000."""
        x_min = math.floor(rect.x0 / page_width * 1000)

        y_min = math.floor(rect.y0 / page_height * 1000)

        x_max = math.ceil(rect.x1 / page_width * 1000)

        y_max = math.ceil(rect.y1 / page_height * 1000)

        x_min = min(
            max(
                x_min,
                0,
            ),
            999,
        )

        y_min = min(
            max(
                y_min,
                0,
            ),
            999,
        )

        x_max = min(
            max(
                x_max,
                x_min + 1,
            ),
            1000,
        )

        y_max = min(
            max(
                y_max,
                y_min + 1,
            ),
            1000,
        )

        return PdfNormalizedBoundingBox(
            x_min=x_min,
            y_min=y_min,
            x_max=x_max,
            y_max=y_max,
        )

    def _render_page(
        self,
        page: fitz.Page,
    ) -> bytes:
        largest_side = max(
            page.rect.width,
            page.rect.height,
        )

        if largest_side <= 0:
            raise PdfProcessingError(
                "Некорректный размер страницы PDF.",
            )

        scale = self._render_max_side / largest_side

        scale = min(
            max(
                scale,
                0.5,
            ),
            3.0,
        )

        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(
                scale,
                scale,
            ),
            alpha=False,
            colorspace=fitz.csRGB,
        )

        return pixmap.tobytes(
            "png",
        )
