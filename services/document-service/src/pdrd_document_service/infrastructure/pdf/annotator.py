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
from pdrd_document_service.infrastructure.pdf.review_style import (
    annotation_palette,
    pinned_font_size,
)
from pdrd_document_service.infrastructure.pdf.sidebar import (
    SidebarCard,
    card_html,
    card_style,
    extend_right,
    plan_cards,
    summary_text,
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
        self._highlight = False

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
            report.findings_label,
            font_size=13.0,
            after=7.0,
        )

        if not report.findings:
            self._write(
                "Замечания не сформированы.",
            )

        for finding in report.findings:
            self._highlight = finding.origin == "manual"
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
        self._highlight = False

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
            href=field.href,
        )

    def _write(
        self,
        text: str,
        *,
        font_size: float = 9.5,
        line_height: float | None = None,
        after: float = 5.0,
        href: str | None = None,
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

                if self._highlight:
                    self._page.draw_rect(
                        fitz.Rect(
                            self._MARGIN - 3.0,
                            self._y - font_size - 2.0,
                            self._PAGE_WIDTH - self._MARGIN + 3.0,
                            self._y + resolved_line_height - font_size - 2.0,
                        ),
                        color=None,
                        fill=annotation_palette("manual").fill,
                    )

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

                if href:
                    self._page.insert_link(
                        {
                            "kind": fitz.LINK_URI,
                            "from": fitz.Rect(
                                self._MARGIN,
                                self._y - font_size,
                                min(
                                    self._PAGE_WIDTH - self._MARGIN,
                                    self._MARGIN
                                    + self._text_width(line, font_size=font_size),
                                ),
                                self._y + 2.0,
                            ),
                            "uri": href,
                        }
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
                            font_size=font_size,
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
                    font_size=font_size,
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
    """Добавляет callout annotations без растрирования исходного PDF."""

    _ANNOTATION_COLOR = (
        0.86,
        0.12,
        0.12,
    )

    _CARD_TEXT_COLOR = (
        0.12,
        0.12,
        0.12,
    )

    _BADGE_TEXT_COLOR = (
        1.0,
        1.0,
        1.0,
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
        """Создаёт PDF с callout annotations и текстовыми report pages."""
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

                by_page: dict[int, list[PdfFindingAnnotation]] = {}
                for annotation in annotations:
                    self._annotation_page(
                        document=document,
                        original_page_count=original_page_count,
                        page_number=annotation.page_number,
                    )
                    by_page.setdefault(annotation.page_number, []).append(annotation)
                for page_number, page_annotations in by_page.items():
                    page = document[page_number - 1]
                    original_rect = fitz.Rect(page.rect)
                    # Фиксируем visual bbox ДО изменения ширины и матриц страницы.
                    regions = [
                        tuple(
                            self._annotation_rect(page=page, bbox=bbox)[0]
                            for bbox in dict.fromkeys(annotation.regions)
                        )
                        for annotation in page_annotations
                    ]
                    base_font = self._card_font_size(page)
                    entries = []
                    for annotation in page_annotations:
                        old_font = base_font
                        if annotation.callout_box is not None:
                            old_card = self._annotation_rect(
                                page=page, bbox=annotation.callout_box
                            )[0]
                            old_font = pinned_font_size(
                                old_card.width, old_card.height, base_font
                            )
                        entries.append(
                            (
                                summary_text(
                                    annotation.title, located=bool(annotation.regions)
                                ),
                                old_font + 1.5,
                            )
                        )
                    cards, extra_width = plan_cards(original_rect, entries)
                    page = extend_right(page, extra_width)
                    for annotation, visual_regions, card in zip(
                        page_annotations, regions, cards, strict=True
                    ):
                        self._add_finding_annotation(
                            page=page,
                            annotation=annotation,
                            visual_regions=visual_regions,
                            card=card,
                        )

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
        page: fitz.Page,
        annotation: PdfFindingAnnotation,
        visual_regions: tuple[fitz.Rect, ...],
        card: SidebarCard,
    ) -> None:
        """Сохраняет исходные области и связывает каждую с карточкой в правой зоне."""
        for visual_region in visual_regions:
            rectangle = page.add_rect_annot(
                self._visual_rect_to_annotation(page=page, rect=visual_region)
            )
            rectangle.set_info(
                title=f"Замечание №{annotation.number}", subject="PDRD Validation"
            )
            rectangle.set_border(width=1.2)
            rectangle.set_colors(stroke=self._ANNOTATION_COLOR)
            rectangle.update(opacity=0.8)
            self._add_connector(
                page=page, region=visual_region, card=card.rect, annotation=annotation
            )
        self._add_callout_card(page=page, annotation=annotation, card=card)

    def _add_connector(
        self,
        *,
        page: fitz.Page,
        region: fitz.Rect,
        card: fitz.Rect,
        annotation: PdfFindingAnnotation,
    ) -> None:
        """Соединяет finding bbox с видимой annotation-card."""
        start, end = self._nearest_connector_points(
            region,
            card,
        )

        annotation_start = self._visual_point_to_annotation(
            page=page,
            point=start,
        )

        annotation_end = self._visual_point_to_annotation(
            page=page,
            point=end,
        )

        connector = page.add_line_annot(
            annotation_start,
            annotation_end,
        )

        connector.set_info(
            title=(f"Замечание №{annotation.number}"),
            subject="PDRD Validation",
        )

        connector.set_border(
            width=1.1,
        )

        connector.set_colors(
            stroke=self._ANNOTATION_COLOR,
        )

        connector.update(
            opacity=0.58,
        )

    def _add_callout_card(
        self,
        *,
        page: fitz.Page,
        annotation: PdfFindingAnnotation,
        card: SidebarCard,
    ) -> None:
        """Рисует светлую PDF-карточку с компактным номером без Help и технических popup."""
        rect, font_size = card.rect, card.font_size
        border = page.add_rect_annot(
            self._visual_rect_to_annotation(page=page, rect=rect)
        )
        border.set_info(
            title=f"Замечание №{annotation.number}", subject="PDRD Validation"
        )
        border.set_border(width=0.7)
        border.set_colors(stroke=self._ANNOTATION_COLOR, fill=(1.0, 0.975, 0.975))
        border.update(opacity=0.96)
        body = fitz.Rect(
            rect.x0 + 6, rect.y0 + font_size * 1.5 + 12, rect.x1 - 6, rect.y1 - 6
        )
        text = page.add_freetext_annot(
            self._visual_rect_to_annotation(page=page, rect=body),
            card_html(card.text),
            fontsize=font_size,
            text_color=self._CARD_TEXT_COLOR,
            border_width=0,
            rotate=page.rotation,
            richtext=True,
            style=card_style(font_size),
        )
        # Удаляем добавленную PyMuPDF пустую строку XML перед первым абзацем.
        # Нативный FreeText и типографика DS сохраняются.
        page.parent.xref_set_key(
            text.xref,
            "RC",
            fitz.get_pdf_str(
                '<body xmlns="http://www.w3.org/1999/xhtml">'
                + card_html(card.text)
                + "</body>"
            ),
        )
        text.update()
        text.set_info(
            title=f"Замечание №{annotation.number}", subject="PDRD Validation"
        )
        badge_rect = fitz.Rect(
            rect.x0 + 6,
            rect.y0 + 6,
            rect.x0 + 6 + max(22, font_size * 2),
            rect.y0 + 6 + font_size * 1.5,
        )
        badge = page.add_freetext_annot(
            self._visual_rect_to_annotation(page=page, rect=badge_rect),
            str(annotation.number),
            fontsize=font_size,
            text_color=self._BADGE_TEXT_COLOR,
            fill_color=self._ANNOTATION_COLOR,
            border_width=0,
            align=1,
            rotate=page.rotation,
        )
        badge.set_info(
            title=f"Замечание №{annotation.number}", subject="PDRD Validation"
        )

    @staticmethod
    def _card_font_size(
        page: fitz.Page,
    ) -> float:
        """Масштабирует visible card font для A4/A3/A1."""
        return max(
            7.0,
            min(
                20.0,
                page.rect.width / 120.0,
            ),
        )

    @staticmethod
    def _nearest_connector_points(
        region: fitz.Rect,
        card: fitz.Rect,
    ) -> tuple[
        fitz.Point,
        fitz.Point,
    ]:
        """Выбирает ближайшие стороны bbox/card для connector."""
        region_center_x = (region.x0 + region.x1) / 2.0

        region_center_y = (region.y0 + region.y1) / 2.0

        card_center_x = (card.x0 + card.x1) / 2.0

        card_center_y = (card.y0 + card.y1) / 2.0

        horizontal_delta = card_center_x - region_center_x

        vertical_delta = card_center_y - region_center_y

        if abs(
            horizontal_delta,
        ) >= abs(
            vertical_delta,
        ):
            return (
                fitz.Point(
                    (region.x1 if horizontal_delta >= 0 else region.x0),
                    region_center_y,
                ),
                fitz.Point(
                    (card.x0 if horizontal_delta >= 0 else card.x1),
                    card_center_y,
                ),
            )

        return (
            fitz.Point(
                region_center_x,
                (region.y1 if vertical_delta >= 0 else region.y0),
            ),
            fitz.Point(
                card_center_x,
                (card.y0 if vertical_delta >= 0 else card.y1),
            ),
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

        return (
            visual_rect,
            (
                PyMuPdfAnnotationWriter._visual_rect_to_annotation(
                    page=page,
                    rect=visual_rect,
                )
            ),
        )

    @staticmethod
    def _visual_rect_to_annotation(
        *,
        page: fitz.Page,
        rect: fitz.Rect,
    ) -> fitz.Rect:
        """Переводит visual rect в annotation coordinate system."""
        if page.rotation:
            return rect * page.derotation_matrix

        return rect

    @staticmethod
    def _visual_point_to_annotation(
        *,
        page: fitz.Page,
        point: fitz.Point,
    ) -> fitz.Point:
        """Переводит visual point в annotation coordinate system."""
        if page.rotation:
            return point * page.derotation_matrix

        return point
