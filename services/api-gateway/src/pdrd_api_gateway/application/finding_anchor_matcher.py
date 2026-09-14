# services/api-gateway/src/pdrd_api_gateway/application/finding_anchor_matcher.py

"""Deterministic localization findings по координатам текста PDF."""

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


@dataclass(frozen=True, slots=True)
class FindingAnchorMatcher:
    """Находит инженерные обозначения finding в PDF text geometry."""

    horizontal_padding: int = 12

    vertical_padding: int = 18

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
        """Возвращает exact text locations без обращения к VLM."""
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
        """Локализует один finding по strong PDF anchors."""
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

    def _word_matches_finding(
        self,
        *,
        word: AnalysisTextWord,
        haystack: str,
    ) -> bool:
        """Проверяет exact strong anchor match."""
        anchor = self._normalize_anchor(
            word.text,
        )

        if not self._is_strong_anchor(
            anchor,
        ):
            return False

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
            .translate(
                _DASH_TRANSLATION,
            )
            .upper()
        )

    @classmethod
    def _normalize_anchor(
        cls,
        value: str,
    ) -> str:
        """Убирает внешнюю пунктуацию, сохраняя структуру designation."""
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
    ) -> bool:
        """Отделяет designations от обычных слов finding."""
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

        if not has_letter:
            return False

        has_digit = (
            re.search(
                r"\d",
                anchor,
            )
            is not None
        )

        if has_digit:
            return True

        return (
            re.fullmatch(
                r"[A-Z]{2,16}",
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

        groups: list[list[AnalysisTextWord]] = []

        current: list[AnalysisTextWord] = []

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
    ) -> AnalysisVisualRegion:
        """Строит padded bbox группы matched designations."""
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

        label = " ".join(word.text for word in group)

        return AnalysisVisualRegion(
            bbox=padded,
            source="pdf_text",
            confidence=0.99,
            label=label,
        )
