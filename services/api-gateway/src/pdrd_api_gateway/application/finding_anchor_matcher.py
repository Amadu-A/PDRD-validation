# services/api-gateway/src/pdrd_api_gateway/application/finding_anchor_matcher.py

"""Гибридная локализация замечаний по VLM-областям и текстовой геометрии PDF."""

import re
import unicodedata
from dataclasses import dataclass

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingLocation,
    AnalysisFindingTarget,
    AnalysisTextWord,
    AnalysisVisualRegion,
)

_DASH_TRANSLATION = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "−": "-",
        "-": "-",
        "‒": "-",
        "﹘": "-",
        "﹣": "-",
        "－": "-",
    }
)

# Проверка относится к факту повторения позиционного обозначения,
# а не к любому присутствию чисел в описании замечания.
_DUPLICATE_WORDS = re.compile(r"дублир\w*|повтор\w*", re.IGNORECASE)
_POSITION_WORDS = re.compile(
    r"позицион\w*|обозначен\w*|позици\w*|номер\w*", re.IGNORECASE
)
_POSITION_TAG = re.compile(r"(?<![\w.])\d+(?:\.\d+){2,4}(?![\w]|\.\d)")
_NUMERIC_FIELD_LABEL = re.compile(
    r"\b(?:номер\w*\s+(?:страниц\w*|лист\w*)|лист\w*|страниц\w*)\b",
    re.IGNORECASE,
)


_HOMOGLYPH_TRANSLATION = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "С": "C",
        "Е": "E",
        "Н": "H",
        "К": "K",
        "М": "M",
        "О": "O",
        "Р": "P",
        "Т": "T",
        "Х": "X",
        "У": "Y",
    }
)


@dataclass(frozen=True, slots=True)
class FindingAnchorMatcher:
    """Локализует finding, сохраняя VLM semantic region приоритетным."""

    horizontal_padding: int = 12

    vertical_padding: int = 18

    scoped_padding: int = 24

    max_regions_per_finding: int = 16

    def locate(
        self,
        *,
        findings: tuple[
            AnalysisFindingTarget,
            ...,
        ],
        text_words: tuple[
            AnalysisTextWord,
            ...,
        ],
    ) -> tuple[
        AnalysisFindingLocation,
        ...,
    ]:
        """Возвращает saved VLM или deterministic PDF text locations."""
        return tuple(
            self._locate_finding(
                finding=finding,
                text_words=text_words,
            )
            for finding in findings
        )

    def _locate_finding(
        self,
        *,
        finding: AnalysisFindingTarget,
        text_words: tuple[
            AnalysisTextWord,
            ...,
        ],
    ) -> AnalysisFindingLocation:
        """Локализует finding без глобального переопределения VLM provenance."""
        repeated_tag = self.duplicate_position_tag(finding)
        if repeated_tag is not None:
            # Канонический текстовый факт допускает все точные подписи.
            # Для обычного VLM-утверждения третья подпись делает пару неясной.
            exact_words = tuple(
                word
                for word in text_words
                if self._normalize_anchor(word.text) == repeated_tag
            )
            canonical_duplicate = (
                re.fullmatch(r"p[1-9]\d*-dpos-\d+(?:-\d+){2,4}", finding.finding_id)
                is not None
            )
            if len(exact_words) < 2 or (
                len(exact_words) > 2 and not canonical_duplicate
            ):
                return AnalysisFindingLocation.unlocated(
                    finding_id=finding.finding_id,
                )
            ordered = sorted(
                exact_words,
                key=lambda word: (word.bbox.y_min, word.bbox.x_min),
            )
            regions = tuple(
                self._region_from_group(
                    (word,),
                    source="pdf_text",
                    confidence=0.99,
                    label=repeated_tag,
                )
                for word in ordered[: self.max_regions_per_finding]
            )
            return AnalysisFindingLocation.located(
                finding_id=finding.finding_id,
                regions=regions,
                confidence=0.99,
                method="pdf_text",
            )

        if finding.visual_regions:
            regions = self._refine_saved_regions(
                finding=finding,
                text_words=text_words,
            )

            return AnalysisFindingLocation.located(
                finding_id=finding.finding_id,
                regions=regions,
                confidence=min(region.confidence for region in regions),
                method="analysis_vlm",
            )

        haystack = self._normalize_text(
            "\n".join(
                (
                    finding.comment,
                    finding.evidence,
                )
            )
        )

        matching_words = tuple(
            word
            for word in text_words
            if self._word_matches_finding(
                word=word,
                haystack=haystack,
            )
        )

        if not matching_words:
            return AnalysisFindingLocation.unlocated(
                finding_id=finding.finding_id,
            )

        groups = self._group_adjacent_words(
            matching_words,
        )

        regions = tuple(
            self._region_from_group(
                group,
                source="pdf_text",
                confidence=0.99,
            )
            for group in groups[: self.max_regions_per_finding]
        )

        if not regions:
            return AnalysisFindingLocation.unlocated(
                finding_id=finding.finding_id,
            )

        return AnalysisFindingLocation.located(
            finding_id=finding.finding_id,
            regions=regions,
            confidence=0.99,
            method="pdf_text",
        )

    @classmethod
    def duplicate_position_tag(
        cls,
        finding: AnalysisFindingTarget,
    ) -> str | None:
        """Возвращает проверяемое обозначение для факта его дублирования.

        Учитывается связная формулировка замечания. Упоминание насоса
        с другим позиционным номером не переопределяет основное обозначение.
        """
        # Канонический ID однозначно задаёт требуемую позиционную подпись.
        canonical = re.fullmatch(
            r"p[1-9]\d*-dpos-(\d+(?:-\d+){2,4})",
            finding.finding_id,
        )
        if canonical is not None:
            return canonical.group(1).replace("-", ".")

        for phrase in (finding.comment, finding.evidence):
            if not (_DUPLICATE_WORDS.search(phrase) and _POSITION_WORDS.search(phrase)):
                continue
            match = _POSITION_TAG.search(phrase)
            if match is not None:
                return match.group(0)
        return None

    def _refine_saved_regions(
        self,
        *,
        finding: AnalysisFindingTarget,
        text_words: tuple[
            AnalysisTextWord,
            ...,
        ],
    ) -> tuple[
        AnalysisVisualRegion,
        ...,
    ]:
        """Уточняет VLM bbox PDF-текстом только внутри самого VLM region."""
        result: list[AnalysisVisualRegion,] = []

        for region in finding.visual_regions:
            label_haystack = self._normalize_text(
                region.label or "",
            )
            if not label_haystack:
                result.append(region)
                continue
            numeric_field_label = bool(_NUMERIC_FIELD_LABEL.search(region.label or ""))

            matching_words = tuple(
                word
                for word in text_words
                if self._word_overlaps_region(
                    word=word,
                    region=region,
                )
                and self._word_matches_scoped_context(
                    word=word,
                    label_haystack=(label_haystack),
                    numeric_field_label=numeric_field_label,
                )
            )

            if not matching_words:
                result.append(
                    region,
                )

                continue

            result.append(
                self._region_from_group(
                    matching_words,
                    source=("analysis_vlm+pdf_text"),
                    confidence=min(
                        1.0,
                        (region.confidence + 0.99) / 2.0,
                    ),
                    label=(
                        region.label or " ".join(word.text for word in matching_words)
                    ),
                )
            )

        return tuple(
            result,
        )

    def _word_matches_finding(
        self,
        *,
        word: AnalysisTextWord,
        haystack: str,
    ) -> bool:
        """Проверяет exact global strong anchor match."""
        anchor = self._normalize_anchor(
            word.text,
        )

        if not self._is_strong_anchor(
            anchor,
            raw_value=word.text,
        ):
            return False

        return self._anchor_in_haystack(
            anchor=anchor,
            haystack=haystack,
        )

    def _word_matches_scoped_context(
        self,
        *,
        word: AnalysisTextWord,
        label_haystack: str,
        numeric_field_label: bool,
    ) -> bool:
        """Разрешает weak numeric anchor только внутри saved VLM region."""
        anchor = self._normalize_anchor(
            word.text,
        )

        if not anchor:
            return False

        if self._is_strong_anchor(
            anchor,
            raw_value=word.text,
        ):
            # Подписанная VLM-область имеет собственный semantic scope.
            # Нельзя сузить резервуарную область по любому совпадению
            # из общего текста замечания, относящемуся к другому объекту.
            return self._anchor_in_haystack(
                anchor=anchor,
                haystack=label_haystack,
            )

        if re.fullmatch(r"\d{1,6}", anchor):
            # Число внутри области сравнения объектов не является точкой
            # замечания. Уточняем только явно названное поле номера/листа.
            return numeric_field_label and self._anchor_in_haystack(
                anchor=anchor,
                haystack=label_haystack,
            )

        return False

    def _word_overlaps_region(
        self,
        *,
        word: AnalysisTextWord,
        region: AnalysisVisualRegion,
    ) -> bool:
        """Проверяет пересечение PDF word с немного расширенным VLM bbox."""
        bbox = region.bbox

        x_min = max(
            0,
            bbox.x_min - self.scoped_padding,
        )

        y_min = max(
            0,
            bbox.y_min - self.scoped_padding,
        )

        x_max = min(
            1000,
            bbox.x_max + self.scoped_padding,
        )

        y_max = min(
            1000,
            bbox.y_max + self.scoped_padding,
        )

        return not (
            word.bbox.x_max < x_min
            or word.bbox.x_min > x_max
            or word.bbox.y_max < y_min
            or word.bbox.y_min > y_max
        )

    @staticmethod
    def _anchor_in_haystack(
        *,
        anchor: str,
        haystack: str,
    ) -> bool:
        """Ищет exact normalized anchor с token boundaries."""
        pattern = (
            r"(?<![0-9A-ZА-ЯЁ])"
            + re.escape(
                anchor,
            )
            + r"(?![0-9A-ZА-ЯЁ])"
        )

        return (
            re.search(
                pattern,
                haystack,
            )
            is not None
        )

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:
        """Нормализует finding text для exact anchor matching."""
        return (
            unicodedata.normalize(
                "NFKC",
                value,
            )
            .upper()
            .translate(
                _DASH_TRANSLATION,
            )
            .translate(
                _HOMOGLYPH_TRANSLATION,
            )
        )

    @classmethod
    def _normalize_anchor(
        cls,
        value: str,
    ) -> str:
        """Убирает внешнюю пунктуацию, сохраняя structure designation."""
        normalized = cls._normalize_text(
            value,
        ).strip()

        normalized = re.sub(
            r"^[^0-9A-ZА-ЯЁ]+",
            "",
            normalized,
        )

        normalized = re.sub(
            r"[^0-9A-ZА-ЯЁ]+$",
            "",
            normalized,
        )

        return normalized

    @staticmethod
    def _is_strong_anchor(
        anchor: str,
        *,
        raw_value: str,
    ) -> bool:
        """Отделяет engineering designations от обычных чисел и слов."""
        if (
            len(
                anchor,
            )
            < 2
            or len(
                anchor,
            )
            > 64
        ):
            return False

        has_letter = (
            re.search(
                r"[A-ZА-ЯЁ]",
                anchor,
            )
            is not None
        )

        has_digit = (
            re.search(
                r"\d",
                anchor,
            )
            is not None
        )

        if has_letter and has_digit:
            return True

        if has_letter:
            raw_anchor = unicodedata.normalize(
                "NFKC",
                raw_value,
            ).strip()

            raw_anchor = re.sub(
                r"^[^A-Za-z]+|[^A-Za-z]+$",
                "",
                raw_anchor,
            )

            return (
                re.fullmatch(
                    r"[A-Za-z]{2,16}",
                    raw_anchor,
                )
                is not None
                and raw_anchor == raw_anchor.upper()
            )

        return (
            re.fullmatch(
                r"\d+(?:\.\d+){1,4}"
                r"(?:-\d+(?:\.\d+){1,4})?",
                anchor,
            )
            is not None
        )

    @staticmethod
    def _group_adjacent_words(
        words: tuple[
            AnalysisTextWord,
            ...,
        ],
    ) -> tuple[
        tuple[
            AnalysisTextWord,
            ...,
        ],
        ...,
    ]:
        """Склеивает соседние matched words одной PDF-строки."""
        ordered = sorted(
            words,
            key=lambda word: (
                word.block_no,
                word.line_no,
                word.word_no,
                word.bbox.x_min,
                word.bbox.y_min,
            ),
        )

        groups: list[list[AnalysisTextWord,],] = []

        current: list[AnalysisTextWord,] = []

        previous: AnalysisTextWord | None = None

        for word in ordered:
            is_adjacent = (
                previous is not None
                and word.block_no == previous.block_no
                and word.line_no == previous.line_no
                and word.word_no == previous.word_no + 1
            )

            if current and not is_adjacent:
                groups.append(
                    current,
                )

                current = []

            current.append(
                word,
            )

            previous = word

        if current:
            groups.append(
                current,
            )

        return tuple(
            tuple(
                group,
            )
            for group in groups
        )

    def _region_from_group(
        self,
        group: tuple[
            AnalysisTextWord,
            ...,
        ],
        *,
        source: str,
        confidence: float,
        label: str | None = None,
    ) -> AnalysisVisualRegion:
        """Строит padded bbox группы matched PDF words."""
        x_min = min(word.bbox.x_min for word in group)

        y_min = min(word.bbox.y_min for word in group)

        x_max = max(word.bbox.x_max for word in group)

        y_max = max(word.bbox.y_max for word in group)

        padded = AnalysisBoundingBox(
            x_min=max(
                x_min - self.horizontal_padding,
                0,
            ),
            y_min=max(
                y_min - self.vertical_padding,
                0,
            ),
            x_max=min(
                x_max + self.horizontal_padding,
                1000,
            ),
            y_max=min(
                y_max + self.vertical_padding,
                1000,
            ),
        )

        resolved_label = label or " ".join(word.text for word in group)

        return AnalysisVisualRegion(
            bbox=padded,
            source=source,
            confidence=confidence,
            label=resolved_label,
        )
