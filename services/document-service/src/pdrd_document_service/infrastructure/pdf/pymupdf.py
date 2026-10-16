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
from pdrd_document_service.domain.specification import join_specification_pages
from pdrd_document_service.infrastructure.pdf.specification import extract_specification


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
        include_equipment_specification: bool = False,
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
                        include_equipment_specification=include_equipment_specification,
                    )
                    for page_number in selected_pages
                )

                if include_equipment_specification:
                    join_specification_pages(
                        [page.equipment_specification for page in pages]
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
        include_equipment_specification: bool = False,
    ) -> PdfPage:
        full_text = page.get_text("text", sort=True)
        text = full_text[: self._text_limit].strip()
        specification = (
            extract_specification(page, page_number, full_text)
            if include_equipment_specification
            else None
        )

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
            equipment_specification=specification,
            graphic_lines=self._extract_graphic_lines(page),
        )

    @staticmethod
    def _extract_graphic_lines(page: fitz.Page) -> tuple[dict, ...]:
        """Читает прямые PDF-пути для проверки выносок в системе координат слов.

        Мелкие штрихи шрифта не используются как линии привязки. Текст, исходное
        изображение, факты и области при этом сохраняются без изменений.
        """
        result = []
        for path_index, path in enumerate(page.get_drawings()):
            if path.get("color") is None:
                continue
            for item_index, item in enumerate(path["items"]):
                if item[0] != "l":
                    continue
                first, second = item[1:3]
                if abs(second - first) < 4:
                    continue
                if page.rotation:
                    first, second = (
                        first * page.rotation_matrix,
                        second * page.rotation_matrix,
                    )
                values = (
                    first.x / page.rect.width * 1000,
                    first.y / page.rect.height * 1000,
                    second.x / page.rect.width * 1000,
                    second.y / page.rect.height * 1000,
                )
                if not all(0 <= value <= 1000 for value in values):
                    continue
                result.append(
                    {
                        "id": f"path-{path_index}-line-{item_index}",
                        **dict(
                            zip(
                                ("x1", "y1", "x2", "y2"),
                                (round(value, 3) for value in values),
                                strict=True,
                            )
                        ),
                        "width": round(float(path.get("width") or 0), 3),
                        "color": list(path["color"]),
                    }
                )
        return tuple(result)

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
