# services/document-service/src/pdrd_document_service/infrastructure/pdf/annotator.py

"""PyMuPDF writer реальных PDF annotations и append-only отчёта."""

import math

import fitz

from pdrd_document_service.application.ports.pdf import (
    PdfProcessingError,
)
from pdrd_document_service.application.ports.pdf_annotation import (
    PdfFindingAnnotation,
    PdfReportField,
    PdfTextReport,
)


class _PdfReportWriter:
    """Минимальный текстовый layout engine для append-only PDF report."""

    _PAGE_WIDTH = 595.0
    _PAGE_HEIGHT = 842.0
    _MARGIN = 42.0
    _FONT_NAME = "PDRDReport"

    def __init__(
        self,
        *,
        document: fitz.Document,
        font: fitz.Font,
    ) -> None:
        """Сохраняет PDF и Unicode font."""
        self._document = document
        self._font = font
        self._page: fitz.Page | None = None
        self._y = 0.0

    def append(
        self,
        report: PdfTextReport,
    ) -> None:
        """Добавляет полный текстовый report в конец документа."""
        self._new_page()

        self._write(
            report.title,
            font_size=16.0,
            line_height=21.0,
            after=10.0,
        )

        for field in report.metadata:
            self._write_field(
                field,
            )

        if report.summary:
            self._y += 5.0

            self._write(
                "Итог",
                font_size=12.0,
                after=4.0,
            )

            self._write(
                report.summary,
                after=9.0,
            )

        self._write(
            "Замечания",
            font_size=13.0,
            after=7.0,
        )

        if not report.findings:
            self._write(
                "Замечания не сформированы.",
            )

        for finding in report.findings:
            self._write(
                finding.title,
                font_size=11.0,
                after=4.0,
            )

            for field in finding.fields:
                self._write_field(
                    field,
                )

            self._y += 6.0

        if report.limitations:
            self._write(
                "Ограничения",
                font_size=12.0,
                after=4.0,
            )

            for limitation in report.limitations:
                self._write(
                    f"• {limitation}",
                    after=2.0,
                )

    def _new_page(
        self,
    ) -> None:
        """Создаёт новую A4-like страницу отчёта."""
        self._page = self._document.new_page(
            width=self._PAGE_WIDTH,
            height=self._PAGE_HEIGHT,
        )

        self._page.insert_font(
            fontname=self._FONT_NAME,
            fontbuffer=self._font.buffer,
        )

        self._y = self._MARGIN

    def _write_field(
        self,
        field: PdfReportField,
    ) -> None:
        """Печатает label/value поле."""
        self._write(
            (f"{field.label}: {field.value}"),
            after=2.5,
        )

    def _write(
        self,
        text: str,
        *,
        font_size: float = 9.5,
        line_height: float | None = None,
        after: float = 5.0,
    ) -> None:
        """Печатает wrapped Unicode text с автоматическими page breaks."""
        if self._page is None:
            self._new_page()

        resolved_line_height = (
            line_height if line_height is not None else font_size * 1.4
        )

        available_width = self._PAGE_WIDTH - (2 * self._MARGIN)

        for line in self._wrap(
            text,
            font_size=font_size,
            available_width=available_width,
        ):
            if self._y + resolved_line_height > self._PAGE_HEIGHT - self._MARGIN:
                self._new_page()

            if line:
                assert self._page is not None

                self._page.insert_text(
                    (
                        self._MARGIN,
                        self._y,
                    ),
                    line,
                    fontname=self._FONT_NAME,
                    fontsize=font_size,
                    color=(
                        0.12,
                        0.12,
                        0.12,
                    ),
                )

            self._y += resolved_line_height

        self._y += after

    def _wrap(
        self,
        text: str,
        *,
        font_size: float,
        available_width: float,
    ) -> tuple[
        str,
        ...,
    ]:
        """Разбивает текст по ширине embedded font."""
        paragraphs = str(
            text,
        ).splitlines()

        if not paragraphs:
            paragraphs = [
                "",
            ]

        result: list[str] = []

        for (
            paragraph_index,
            paragraph,
        ) in enumerate(
            paragraphs,
        ):
            words: list[str] = []

            for token in paragraph.split():
                words.extend(
                    self._split_long_token(
                        token,
                        font_size=font_size,
                        available_width=(available_width),
                    )
                )

            if not words:
                result.append(
                    "",
                )

            else:
                current = words[0]

                for word in words[1:]:
                    candidate = f"{current} {word}" if current else word

                    if (
                        self._text_width(
                            candidate,
                            font_size=(font_size),
                        )
                        <= available_width
                    ):
                        current = candidate

                    else:
                        result.append(
                            current,
                        )

                        current = word

                result.append(
                    current,
                )

            if (
                paragraph_index
                < len(
                    paragraphs,
                )
                - 1
            ):
                result.append(
                    "",
                )

        return tuple(
            result,
        )

    def _split_long_token(
        self,
        token: str,
        *,
        font_size: float,
        available_width: float,
    ) -> tuple[
        str,
        ...,
    ]:
        """Безопасно режет UUID/URL-like token, который шире строки."""
        if (
            self._text_width(
                token,
                font_size=font_size,
            )
            <= available_width
        ):
            return (token,)

        chunks: list[str] = []

        current = ""

        for character in token:
            candidate = current + character

            if (
                current
                and self._text_width(
                    candidate,
                    font_size=(font_size),
                )
                > available_width
            ):
                chunks.append(
                    current,
                )

                current = character

            else:
                current = candidate

        if current:
            chunks.append(
                current,
            )

        return tuple(
            chunks,
        )

    def _text_width(
        self,
        text: str,
        *,
        font_size: float,
    ) -> float:
        """Возвращает ширину строки в points."""
        return float(
            self._font.text_length(
                text,
                fontsize=font_size,
            )
        )


class PyMuPdfAnnotationWriter:
    """Добавляет annotations без растрирования исходных PDF-страниц."""

    _ANNOTATION_COLOR = (
        0.86,
        0.12,
        0.12,
    )

    _LABEL_TEXT_COLOR = (
        0.75,
        0.05,
        0.05,
    )

    _LABEL_FILL_COLOR = (
        1.0,
        0.96,
        0.78,
    )

    def build(
        self,
        *,
        content: bytes,
        annotations: tuple[
            PdfFindingAnnotation,
            ...,
        ],
        report: PdfTextReport,
    ) -> bytes:
        """Создаёт PDF с native annotations и текстовыми report pages."""
        try:
            with fitz.open(
                stream=content,
                filetype="pdf",
            ) as document:
                original_page_count = document.page_count

                if original_page_count < 1:
                    raise PdfProcessingError(
                        ("Исходный PDF не содержит страниц."),
                    )

                page_level_slots: dict[
                    int,
                    int,
                ] = {}

                for annotation in annotations:
                    if annotation.regions:
                        self._add_finding_annotation(
                            document=document,
                            original_page_count=(original_page_count),
                            annotation=annotation,
                        )

                        continue

                    slot = page_level_slots.get(
                        annotation.page_number,
                        0,
                    )

                    self._add_page_level_annotation(
                        document=document,
                        original_page_count=(original_page_count),
                        annotation=annotation,
                        slot=slot,
                    )

                    page_level_slots[annotation.page_number] = slot + 1

                report_font = fitz.Font(
                    "cjk",
                )

                report_writer = _PdfReportWriter(
                    document=document,
                    font=report_font,
                )

                report_writer.append(
                    report,
                )

                result = document.tobytes(
                    garbage=3,
                    deflate=True,
                )

        except PdfProcessingError:
            raise

        except Exception as error:
            raise PdfProcessingError(
                ("Не удалось сформировать PDF с аннотациями."),
            ) from error

        if not result.startswith(
            b"%PDF-",
        ):
            raise PdfProcessingError(
                ("Сформированный annotation export не является PDF."),
            )

        return result

    def _add_finding_annotation(
        self,
        *,
        document: fitz.Document,
        original_page_count: int,
        annotation: PdfFindingAnnotation,
    ) -> None:
        """Добавляет все visual regions одного finding."""
        page = self._annotation_page(
            document=document,
            original_page_count=(original_page_count),
            page_number=(annotation.page_number),
        )

        for (
            region_index,
            bbox,
        ) in enumerate(
            annotation.regions,
            start=1,
        ):
            (
                visual_rect,
                annotation_rect,
            ) = self._annotation_rect(
                page=page,
                bbox=bbox,
            )

            rectangle_annotation = page.add_rect_annot(
                annotation_rect,
            )

            rectangle_annotation.set_info(
                title=annotation.title,
                content=annotation.content,
                subject="PDRD Validation",
            )

            rectangle_annotation.set_border(
                width=1.2,
            )

            rectangle_annotation.set_colors(
                stroke=(self._ANNOTATION_COLOR),
            )

            rectangle_annotation.update(
                opacity=0.35,
            )

            if (
                len(
                    annotation.regions,
                )
                == 1
            ):
                label = f"[{annotation.number}]"

            else:
                label = f"[{annotation.number}.{region_index}]"

            label_rect = self._label_rect(
                page=page,
                visual_rect=visual_rect,
                label=label,
            )

            label_annotation = page.add_freetext_annot(
                label_rect,
                label,
                fontsize=7.0,
                text_color=(self._LABEL_TEXT_COLOR),
                fill_color=(self._LABEL_FILL_COLOR),
                border_width=0.4,
                opacity=0.92,
            )

            label_annotation.set_info(
                title=annotation.title,
                content=annotation.content,
                subject="PDRD Validation",
            )

    def _add_page_level_annotation(
        self,
        *,
        document: fitz.Document,
        original_page_count: int,
        annotation: PdfFindingAnnotation,
        slot: int,
    ) -> None:
        """Добавляет marker без fake bbox, если точное место неизвестно."""
        page = self._annotation_page(
            document=document,
            original_page_count=(original_page_count),
            page_number=(annotation.page_number),
        )

        (
            visual_badge_rect,
            badge_rect,
        ) = self._page_level_badge_rect(
            page=page,
            slot=slot,
        )

        label = f"PDRD [{annotation.number}]"

        badge = page.add_freetext_annot(
            badge_rect,
            label,
            fontsize=7.0,
            text_color=(self._LABEL_TEXT_COLOR),
            fill_color=(self._LABEL_FILL_COLOR),
            border_width=0.7,
            opacity=0.92,
        )

        # Для FreeText content должен оставаться самим
        # отображаемым label. Полный текст finding хранит
        # отдельная native Comment annotation ниже.
        badge.set_info(
            title=annotation.title,
            subject="PDRD Validation",
        )

        visual_note_point = fitz.Point(
            min(
                page.rect.x1 - 12.0,
                visual_badge_rect.x1 + 4.0,
            ),
            min(
                page.rect.y1 - 12.0,
                visual_badge_rect.y0 + 4.0,
            ),
        )

        note_point = (
            visual_note_point * page.derotation_matrix
            if page.rotation
            else visual_note_point
        )

        note = page.add_text_annot(
            note_point,
            annotation.content,
            icon="Comment",
        )

        note.set_info(
            title=annotation.title,
            subject="PDRD Validation",
        )

    @staticmethod
    def _annotation_page(
        *,
        document: fitz.Document,
        original_page_count: int,
        page_number: int,
    ) -> fitz.Page:
        """Возвращает исходную PDF-страницу finding."""
        if not (1 <= page_number <= original_page_count):
            raise PdfProcessingError(
                (f"Finding ссылается на несуществующую PDF-страницу: {page_number}."),
            )

        return document[page_number - 1]

    @staticmethod
    def _page_level_badge_rect(
        *,
        page: fitz.Page,
        slot: int,
    ) -> tuple[
        fitz.Rect,
        fitz.Rect,
    ]:
        """Размещает compact page-level marker в верхнем rail листа."""
        page_rect = page.rect

        margin = 10.0
        gap = 6.0
        preferred_width = 72.0
        badge_height = 16.0

        available_width = max(
            page_rect.width - (2 * margin),
            1.0,
        )

        badge_width = min(
            preferred_width,
            available_width,
        )

        columns = max(
            1,
            int((available_width + gap) // (badge_width + gap)),
        )

        column = slot % columns

        row = slot // columns

        x = page_rect.x0 + margin + column * (badge_width + gap)

        y = page_rect.y0 + margin + row * (badge_height + gap)

        x = min(
            x,
            max(
                page_rect.x0,
                page_rect.x1 - margin - badge_width,
            ),
        )

        y = min(
            y,
            max(
                page_rect.y0,
                page_rect.y1 - margin - badge_height,
            ),
        )

        visual_badge_rect = fitz.Rect(
            x,
            y,
            min(
                page_rect.x1,
                x + badge_width,
            ),
            min(
                page_rect.y1,
                y + badge_height,
            ),
        )

        badge_rect = (
            visual_badge_rect * page.derotation_matrix
            if page.rotation
            else visual_badge_rect
        )

        return (
            visual_badge_rect,
            badge_rect,
        )

    @staticmethod
    def _annotation_rect(
        *,
        page: fitz.Page,
        bbox: object,
    ) -> tuple[
        fitz.Rect,
        fitz.Rect,
    ]:
        """Переводит normalized visual bbox в unrotated PDF coordinates."""
        page_rect = page.rect

        page_width = float(
            page_rect.width,
        )

        page_height = float(
            page_rect.height,
        )

        if (
            not math.isfinite(
                page_width,
            )
            or not math.isfinite(
                page_height,
            )
            or page_width <= 0
            or page_height <= 0
        ):
            raise PdfProcessingError(
                ("PDF-страница имеет некорректный размер."),
            )

        x_min = float(
            bbox.x_min,
        )

        y_min = float(
            bbox.y_min,
        )

        x_max = float(
            bbox.x_max,
        )

        y_max = float(
            bbox.y_max,
        )

        visual_rect = fitz.Rect(
            (page_rect.x0 + (x_min / 1000.0 * page_width)),
            (page_rect.y0 + (y_min / 1000.0 * page_height)),
            (page_rect.x0 + (x_max / 1000.0 * page_width)),
            (page_rect.y0 + (y_max / 1000.0 * page_height)),
        )

        visual_rect = visual_rect & page_rect

        if visual_rect.is_empty or visual_rect.is_infinite:
            raise PdfProcessingError(
                ("Finding содержит пустой PDF bbox."),
            )

        annotation_rect = (
            visual_rect * page.derotation_matrix if page.rotation else visual_rect
        )

        return (
            visual_rect,
            annotation_rect,
        )

    @staticmethod
    def _label_rect(
        *,
        page: fitz.Page,
        visual_rect: fitz.Rect,
        label: str,
    ) -> fitz.Rect:
        """Строит compact label около visual bbox."""
        page_rect = page.rect

        height = 14.0

        width = max(
            30.0,
            min(
                52.0,
                (
                    12.0
                    + (
                        7.0
                        * len(
                            label,
                        )
                    )
                ),
            ),
        )

        x = min(
            max(
                page_rect.x0,
                visual_rect.x0,
            ),
            max(
                page_rect.x0,
                page_rect.x1 - width,
            ),
        )

        y = visual_rect.y0 - height - 2.0

        if y < page_rect.y0:
            y = min(
                page_rect.y1 - height,
                visual_rect.y1 + 2.0,
            )

        visual_label_rect = fitz.Rect(
            x,
            y,
            min(
                page_rect.x1,
                x + width,
            ),
            min(
                page_rect.y1,
                y + height,
            ),
        )

        if page.rotation:
            return visual_label_rect * page.derotation_matrix

        return visual_label_rect
