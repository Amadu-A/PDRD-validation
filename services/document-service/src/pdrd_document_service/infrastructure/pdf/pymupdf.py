# services/document-service/src/pdrd_document_service/infrastructure/pdf/pymupdf.py

"""PyMuPDF adapter для чтения и рендеринга PDF."""

import fitz

from pdrd_document_service.application.ports.pdf import (
    PdfProcessingError,
)
from pdrd_document_service.domain.pdf import (
    PdfDocument,
    PdfPage,
    classify_page,
)
from pdrd_document_service.domain.project_context import PdfTextPage


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
        selected_pages: tuple[int, ...],
    ) -> PdfDocument:
        """Извлекает текст и PNG выбранных страниц."""
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
        selected_pages: tuple[int, ...],
    ) -> tuple[PdfTextPage, ...]:
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
