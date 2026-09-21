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

    _CARD_FILL_COLOR = (
        1.0,
        0.92,
        0.92,
    )

    _BADGE_TEXT_COLOR = (
        1.0,
        1.0,
        1.0,
    )

    _BADGE_FILL_COLOR = (
        0.86,
        0.12,
        0.12,
    )

    _CARD_OVERLAP_WEIGHT = 12.0
    _REGION_OVERLAP_WEIGHT = 5.0
    _DISTANCE_WEIGHT = 0.015

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

                all_regions_by_page = self._all_visual_regions(
                    document=document,
                    original_page_count=(original_page_count),
                    annotations=annotations,
                )

                occupied_cards: dict[
                    int,
                    list[fitz.Rect],
                ] = {}

                for annotation in annotations:
                    page_cards = occupied_cards.setdefault(
                        annotation.page_number,
                        [],
                    )

                    self._add_finding_annotation(
                        document=document,
                        original_page_count=(original_page_count),
                        annotation=annotation,
                        occupied_cards=page_cards,
                        all_regions=(
                            all_regions_by_page.get(
                                annotation.page_number,
                                (),
                            )
                        ),
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

    def _all_visual_regions(
        self,
        *,
        document: fitz.Document,
        original_page_count: int,
        annotations: tuple[
            PdfFindingAnnotation,
            ...,
        ],
    ) -> dict[
        int,
        tuple[
            fitz.Rect,
            ...,
        ],
    ]:
        """Собирает bbox всех findings для collision-aware card layout."""
        result: dict[
            int,
            list[fitz.Rect],
        ] = {}

        for annotation in annotations:
            page = self._annotation_page(
                document=document,
                original_page_count=(original_page_count),
                page_number=annotation.page_number,
            )

            page_regions = result.setdefault(
                annotation.page_number,
                [],
            )

            for bbox in annotation.regions:
                visual_rect, _ = self._annotation_rect(
                    page=page,
                    bbox=bbox,
                )

                page_regions.append(
                    visual_rect,
                )

        return {
            page_number: tuple(
                regions,
            )
            for (
                page_number,
                regions,
            ) in result.items()
        }

    def _add_finding_annotation(
        self,
        *,
        document: fitz.Document,
        original_page_count: int,
        annotation: PdfFindingAnnotation,
        occupied_cards: list[fitz.Rect],
        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> None:
        """Добавляет bbox, connector, card и native info одного finding."""
        page = self._annotation_page(
            document=document,
            original_page_count=(original_page_count),
            page_number=annotation.page_number,
        )

        region_pairs = tuple(
            self._annotation_rect(
                page=page,
                bbox=bbox,
            )
            for bbox in annotation.regions
        )

        visual_regions = tuple(
            visual_rect
            for (
                visual_rect,
                _,
            ) in region_pairs
        )

        for (
            _,
            annotation_rect,
        ) in region_pairs:
            rectangle_annotation = page.add_rect_annot(
                annotation_rect,
            )

            rectangle_annotation.set_info(
                title=(f"Замечание №{annotation.number}"),
                content=annotation.content,
                subject="PDRD Validation",
            )

            rectangle_annotation.set_border(
                width=1.2,
            )

            rectangle_annotation.set_colors(
                stroke=self._ANNOTATION_COLOR,
            )

            rectangle_annotation.update(
                opacity=0.35,
            )

        anchor = self._union_rect(
            regions=visual_regions,
            page_rect=page.rect,
        )

        card_rect = self._place_card(
            page=page,
            anchor=anchor,
            occupied_cards=occupied_cards,
            all_regions=all_regions,
        )

        for visual_region in visual_regions:
            self._add_connector(
                page=page,
                region=visual_region,
                card=card_rect,
                annotation=annotation,
            )

        self._add_callout_card(
            page=page,
            annotation=annotation,
            visual_card_rect=card_rect,
        )

        occupied_cards.append(
            card_rect,
        )

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
            content=annotation.content,
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
        visual_card_rect: fitz.Rect,
    ) -> None:
        """Добавляет visible card, number badge и native Help annotation."""
        card_rect = self._visual_rect_to_annotation(
            page=page,
            rect=visual_card_rect,
        )

        border = page.add_rect_annot(
            card_rect,
        )

        border.set_info(
            title=(f"Замечание №{annotation.number}"),
            content=annotation.content,
            subject="PDRD Validation",
        )

        border.set_border(
            width=1.0,
        )

        border.set_colors(
            stroke=self._ANNOTATION_COLOR,
        )

        border.update(
            opacity=0.82,
        )

        font_size = self._card_font_size(
            page,
        )

        visible_text = self._visible_card_text(
            annotation.title,
            page=page,
        )

        card = page.add_freetext_annot(
            card_rect,
            ("     " + visible_text),
            fontsize=font_size,
            text_color=self._CARD_TEXT_COLOR,
            fill_color=self._CARD_FILL_COLOR,
            border_width=0,
            opacity=0.90,
        )

        # Не записываем full content в FreeText:
        # некоторые PDF viewers заменяют им appearance text.
        card.set_info(
            title=(f"Замечание №{annotation.number}"),
            subject="PDRD Validation",
        )

        badge_size = max(
            14.0,
            min(
                30.0,
                font_size * 1.65,
            ),
        )

        visual_badge_rect = fitz.Rect(
            visual_card_rect.x0 + 4.0,
            visual_card_rect.y0 + 4.0,
            visual_card_rect.x0 + 4.0 + badge_size,
            visual_card_rect.y0 + 4.0 + badge_size,
        )

        badge_rect = self._visual_rect_to_annotation(
            page=page,
            rect=visual_badge_rect,
        )

        badge = page.add_freetext_annot(
            badge_rect,
            str(
                annotation.number,
            ),
            fontsize=max(
                6.0,
                font_size * 0.72,
            ),
            text_color=self._BADGE_TEXT_COLOR,
            fill_color=self._BADGE_FILL_COLOR,
            border_width=0,
            align=1,
            opacity=0.98,
        )

        badge.set_info(
            title=(f"Замечание №{annotation.number}"),
            subject="PDRD Validation",
        )

        visual_info_point = fitz.Point(
            max(
                visual_card_rect.x0 + 10.0,
                visual_card_rect.x1 - 14.0,
            ),
            min(
                page.rect.y1 - 10.0,
                visual_card_rect.y0 + 12.0,
            ),
        )

        info_point = self._visual_point_to_annotation(
            page=page,
            point=visual_info_point,
        )

        info = page.add_text_annot(
            info_point,
            annotation.content,
            icon="Help",
        )

        info.set_info(
            title=(f"Замечание №{annotation.number}"),
            subject="PDRD Validation",
        )

    def _place_card(
        self,
        *,
        page: fitz.Page,
        anchor: fitz.Rect,
        occupied_cards: list[fitz.Rect],
        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> fitz.Rect:
        """Выбирает свободное место card рядом с finding bbox."""
        width, height = self._card_dimensions(
            page,
        )

        candidates = self._candidate_rects(
            anchor=anchor,
            width=width,
            height=height,
            page_rect=page.rect,
        )

        if not candidates:
            return self._clamp_card_rect(
                left=page.rect.x0 + 10.0,
                top=page.rect.y0 + 10.0,
                width=width,
                height=height,
                page_rect=page.rect,
            )

        return min(
            candidates,
            key=lambda candidate: self._candidate_score(
                candidate=candidate,
                anchor=anchor,
                occupied_cards=(occupied_cards),
                all_regions=all_regions,
            ),
        )

    def _candidate_rects(
        self,
        *,
        anchor: fitz.Rect,
        width: float,
        height: float,
        page_rect: fitz.Rect,
    ) -> tuple[
        fitz.Rect,
        ...,
    ]:
        """Строит near-anchor и grid candidates подобно browser overlay."""
        margin = 10.0
        gap = 14.0

        center_x = (anchor.x0 + anchor.x1) / 2.0

        center_y = (anchor.y0 + anchor.y1) / 2.0

        raw_candidates: list[
            tuple[
                float,
                float,
            ]
        ] = [
            (
                anchor.x1 + gap,
                center_y - height / 2.0,
            ),
            (
                anchor.x0 - width - gap,
                center_y - height / 2.0,
            ),
            (
                center_x - width / 2.0,
                anchor.y1 + gap,
            ),
            (
                center_x - width / 2.0,
                anchor.y0 - height - gap,
            ),
            (
                anchor.x1 + gap,
                anchor.y0 - height - gap,
            ),
            (
                anchor.x1 + gap,
                anchor.y1 + gap,
            ),
            (
                anchor.x0 - width - gap,
                anchor.y0 - height - gap,
            ),
            (
                anchor.x0 - width - gap,
                anchor.y1 + gap,
            ),
        ]

        for factor in (
            -1.35,
            -0.7,
            0.0,
            0.7,
            1.35,
        ):
            raw_candidates.append(
                (
                    (page_rect.x1 - width - margin),
                    (center_y - height / 2.0 + factor * (height + gap)),
                )
            )

            raw_candidates.append(
                (
                    page_rect.x0 + margin,
                    (center_y - height / 2.0 + factor * (height + gap)),
                )
            )

        horizontal_step = max(
            width + gap,
            1.0,
        )

        vertical_step = max(
            height + gap,
            1.0,
        )

        top = page_rect.y0 + margin

        while top <= page_rect.y1 - height - margin:
            left = page_rect.x0 + margin

            while left <= page_rect.x1 - width - margin:
                raw_candidates.append(
                    (
                        left,
                        top,
                    )
                )

                left += horizontal_step

            top += vertical_step

        unique: dict[
            tuple[
                int,
                int,
            ],
            fitz.Rect,
        ] = {}

        for left, top in raw_candidates:
            rect = self._clamp_card_rect(
                left=left,
                top=top,
                width=width,
                height=height,
                page_rect=page_rect,
            )

            key = (
                round(
                    rect.x0,
                ),
                round(
                    rect.y0,
                ),
            )

            unique.setdefault(
                key,
                rect,
            )

        return tuple(
            unique.values(),
        )

    def _candidate_score(
        self,
        *,
        candidate: fitz.Rect,
        anchor: fitz.Rect,
        occupied_cards: list[fitz.Rect],
        all_regions: tuple[
            fitz.Rect,
            ...,
        ],
    ) -> float:
        """Штрафует card-card/card-region overlap и большую дистанцию."""
        card_overlap = sum(
            self._intersection_area(
                candidate,
                occupied,
            )
            for occupied in occupied_cards
        )

        region_overlap = sum(
            self._intersection_area(
                candidate,
                region,
            )
            for region in all_regions
        )

        distance = self._distance_between_centers(
            candidate,
            anchor,
        )

        return (
            card_overlap * self._CARD_OVERLAP_WEIGHT
            + region_overlap * self._REGION_OVERLAP_WEIGHT
            + distance * self._DISTANCE_WEIGHT
        )

    @staticmethod
    def _clamp_card_rect(
        *,
        left: float,
        top: float,
        width: float,
        height: float,
        page_rect: fitz.Rect,
    ) -> fitz.Rect:
        """Удерживает card внутри visual page."""
        margin = 10.0

        safe_left = min(
            max(
                left,
                page_rect.x0 + margin,
            ),
            max(
                page_rect.x0 + margin,
                (page_rect.x1 - width - margin),
            ),
        )

        safe_top = min(
            max(
                top,
                page_rect.y0 + margin,
            ),
            max(
                page_rect.y0 + margin,
                (page_rect.y1 - height - margin),
            ),
        )

        return fitz.Rect(
            safe_left,
            safe_top,
            min(
                page_rect.x1 - margin,
                safe_left + width,
            ),
            min(
                page_rect.y1 - margin,
                safe_top + height,
            ),
        )

    @staticmethod
    def _card_dimensions(
        page: fitz.Page,
    ) -> tuple[
        float,
        float,
    ]:
        """Масштабирует callout относительно PDF-листа."""
        width = min(
            max(
                page.rect.width * 0.22,
                130.0,
            ),
            520.0,
        )

        height = min(
            max(
                page.rect.height * 0.12,
                78.0,
            ),
            210.0,
        )

        width = min(
            width,
            max(
                page.rect.width - 20.0,
                20.0,
            ),
        )

        height = min(
            height,
            max(
                page.rect.height - 20.0,
                20.0,
            ),
        )

        return (
            width,
            height,
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
    def _visible_card_text(
        value: str,
        *,
        page: fitz.Page,
    ) -> str:
        """Ограничивает visible PDF text; full text остаётся в Help popup."""
        normalized_lines = [
            " ".join(line.split())
            for line in str(
                value,
            ).splitlines()
            if line.strip()
        ]

        normalized = "\n".join(
            normalized_lines,
        )

        limit = max(
            100,
            min(
                360,
                int(page.rect.width / 6.0),
            ),
        )

        if (
            len(
                normalized,
            )
            <= limit
        ):
            return normalized

        return normalized[: limit - 1].rstrip() + "…"

    @staticmethod
    def _union_rect(
        *,
        regions: tuple[
            fitz.Rect,
            ...,
        ],
        page_rect: fitz.Rect,
    ) -> fitz.Rect:
        """Возвращает union bbox или центр page для truly-unlocated finding."""
        if not regions:
            center = fitz.Point(
                (page_rect.x0 + page_rect.x1) / 2.0,
                (page_rect.y0 + page_rect.y1) / 2.0,
            )

            return fitz.Rect(
                center.x,
                center.y,
                center.x,
                center.y,
            )

        return fitz.Rect(
            min(region.x0 for region in regions),
            min(region.y0 for region in regions),
            max(region.x1 for region in regions),
            max(region.y1 for region in regions),
        )

    @staticmethod
    def _intersection_area(
        first: fitz.Rect,
        second: fitz.Rect,
    ) -> float:
        """Возвращает площадь пересечения двух visual rect."""
        intersection = first & second

        if intersection.is_empty:
            return 0.0

        return max(
            0.0,
            intersection.width,
        ) * max(
            0.0,
            intersection.height,
        )

    @staticmethod
    def _distance_between_centers(
        first: fitz.Rect,
        second: fitz.Rect,
    ) -> float:
        """Возвращает Euclidean distance между центрами rect."""
        first_x = (first.x0 + first.x1) / 2.0

        first_y = (first.y0 + first.y1) / 2.0

        second_x = (second.x0 + second.x1) / 2.0

        second_y = (second.y0 + second.y1) / 2.0

        return math.hypot(
            first_x - second_x,
            first_y - second_y,
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
