# services/analysis-service/src/pdrd_analysis_service/application/use_cases/duplicate_positions.py

"""Осторожное восстановление повторных обозначений из текстового слоя схемы.

Модуль обнаруживает факты для инженерной проверки, а не автоматически
подтверждённые нарушения. Геометрическое подтверждение выполняется отдельно.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pdrd_analysis_service.application.use_cases.common import (
    ViolationCandidateSelection,
)

logger = logging.getLogger("uvicorn.error")

_POSITION_IN_TEXT = re.compile(r"(?<![\w.])\d+(?:\.\d+){2,4}(?![\w]|\.\d)")
_DUPLICATE_WORDS = re.compile(r"дублир\w*|повтор\w*", re.IGNORECASE)
_POSITION_WORDS = re.compile(
    r"позицион\w*|обозначен\w*|позици\w*|номер\w*", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class RecoveredPositions:
    """Объединённые кандидаты и обозначения, подтверждённые исходным текстом."""

    selection: ViolationCandidateSelection
    tags: tuple[str, ...]
    synthetic_count: int
    normalized_count: int


def is_protected_duplicate_id(finding_id: str) -> bool:
    """Проверяет синтаксис ID детерминированного замечания о повторе."""
    return re.fullmatch(r"p[1-9]\d*-dpos-\d+(?:-\d+){2,4}", finding_id) is not None


def duplicate_position_id(page_number: int, tag: str) -> str:
    """Формирует устойчивый идентификатор независимо от порядка VLM-ответов."""
    return f"p{page_number}-dpos-{tag.replace('.', '-')}"


def _scheme_context(extracted_text: str, page_type: str) -> bool:
    """Не считает ссылки спецификации самостоятельными элементами схемы."""
    if "схем" in page_type.casefold() or page_type.casefold() == "scheme":
        return True
    return (
        re.search(
            r"(?im)^[ \t]*(?:\d{1,3}[ \t]{2,})?"
            r"(?:принципиальн\w*[ \t]+|функциональн\w*[ \t]+"
            r"|технологическ\w*[ \t]+)?схема\b",
            extracted_text,
        )
        is not None
    )


def repeated_standalone_positions(
    extracted_text: str, *, page_type: str
) -> tuple[tuple[str, int], ...]:
    """Считает визуально отделённые подписи в текстовом слое PDF-схемы."""
    # В текст проверки может быть добавлен контекст из пояснительной записки.
    # Источником факта остаётся только текст самой PDF-страницы.
    page_marker = "=== ТЕКСТ АНАЛИЗИРУЕМОЙ СТРАНИЦЫ ===\n"
    if extracted_text.startswith(page_marker):
        extracted_text = extracted_text[len(page_marker) :].split(
            "\n\n=== РЕЛЕВАНТНЫЙ КОНТЕКСТ ", 1
        )[0]
    if not _scheme_context(extracted_text, page_type):
        return ()
    counts: Counter[str] = Counter()
    for raw_line in extracted_text.splitlines():
        line = raw_line.strip()
        for match in _POSITION_IN_TEXT.finditer(line):
            # PyMuPDF sort=True склеивает подписи разных объектов в одну
            # строку с широкими пробелами. Обычные ссылки внутри фразы
            # отделены только одним пробелом и не являются подписью схемы.
            left_isolated = match.start() == 0 or line[: match.start()].endswith("  ")
            right_isolated = match.end() == len(line) or line[match.end() :].startswith(
                "  "
            )
            if left_isolated and right_isolated:
                counts[match.group()] += 1
    repeated = sorted(
        ((tag, count) for tag, count in counts.items() if count >= 2),
        key=lambda item: tuple(int(part) for part in item[0].split(".")),
    )
    # Лимит числа кандидатов не должен безвозвратно отбрасывать
    # реально присутствующие повторные обозначения. Результат ограничен
    # фактическим количеством различных надписей на текущей странице.
    return tuple(repeated)


def _candidate_duplicate_tag(
    candidate: dict[str, Any], observed: frozenset[str]
) -> str | None:
    """Извлекает основное обозначение, а не номера объектов из пояснения."""
    comment = str(candidate.get("comment", ""))
    if not _DUPLICATE_WORDS.search(comment) or not _POSITION_WORDS.search(comment):
        return None
    matches = _POSITION_IN_TEXT.findall(comment)
    if not matches:
        return None
    primary = matches[0]
    return primary if primary in observed else None


def _unverified_duplicate_tag(
    candidate: dict[str, Any], observed: frozenset[str]
) -> str | None:
    """Находит утверждение о повторе без второго текстового подтверждения."""
    comment = str(candidate.get("comment", ""))
    if not _DUPLICATE_WORDS.search(comment) or not _POSITION_WORDS.search(comment):
        return None
    matches = _POSITION_IN_TEXT.findall(comment)
    if not matches or matches[0] in observed:
        return None
    if any(
        candidate.get(key)
        for key in (
            "normative_source_ids",
            "technical_assignment_source_ids",
            "user_package_source_ids",
        )
    ):
        return None
    return matches[0]


def _canonical_candidate(tag: str, count: int) -> dict[str, Any]:
    """Формулирует только доказанный текстом факт, не придумывая объекты."""
    return {
        "category": "marking",
        "severity": "warning",
        "status": "needs_review",
        "comment": f"Проверить повторное позиционное обозначение {tag}.",
        "evidence": (
            f"В текстовом слое схемы отдельная подпись {tag} встречается "
            f"{count} раза. Проверить, относятся ли подписи к разным элементам."
        ),
        "recommendation_draft": (
            "Сопоставить элементы с повторяющейся подписью; при необходимости "
            "исправить обозначения."
        ),
        "confidence": 0.65,
        "normative_source_ids": [],
        "technical_assignment_source_ids": [],
        "user_package_source_ids": [],
        "visual_regions": [],
        "__duplicate_position_tag": tag,
    }


def recover_duplicate_positions(
    *,
    selection: ViolationCandidateSelection,
    extracted_text: str,
    page_type: str,
    page_number: int,
) -> RecoveredPositions:
    """Объединяет формулировки одного повтора и восстанавливает пропуски VLM.

    Сохраняет происхождение исходных кандидатов. Для обнаруженного текстом
    нового обозначения добавляется самостоятельный source index.
    """
    observed_pairs = repeated_standalone_positions(extracted_text, page_type=page_type)
    observed = dict(observed_pairs)
    observed_keys = frozenset(observed)
    is_scheme = _scheme_context(extracted_text, page_type)
    found_tags: set[str] = set()
    candidates: list[dict[str, Any]] = []
    source_indexes: list[tuple[int, ...]] = []
    tag_positions: dict[str, int] = {}
    normalized_count = 0
    rejected_reasons = list(selection.rejected_reasons)

    for candidate, indexes in zip(
        selection.candidates, selection.source_indexes_by_candidate, strict=True
    ):
        unverified_tag = (
            _unverified_duplicate_tag(candidate, observed_keys) if is_scheme else None
        )
        if unverified_tag is not None:
            rejected_reasons.extend("unverified_duplicate_position" for _ in indexes)
            logger.info(
                "duplicate_position_unverified page=%s tag=%s raw_indexes=%s",
                page_number,
                unverified_tag,
                indexes,
            )
            continue
        tag = _candidate_duplicate_tag(candidate, observed_keys)
        if tag is None:
            candidates.append(dict(candidate))
            source_indexes.append(indexes)
            continue
        # Кандидат с N/T/U не заменяется текстовым замечанием без источников.
        # Он отдельно проходит исходную семантическую финализацию.
        if any(
            candidate.get(key)
            for key in (
                "normative_source_ids",
                "technical_assignment_source_ids",
                "user_package_source_ids",
            )
        ):
            candidates.append(dict(candidate))
            source_indexes.append(indexes)
            logger.info(
                "duplicate_position_source_backed_preserved page=%s tag=%s",
                page_number,
                tag,
            )
            continue
        normalized_count += 1
        found_tags.add(tag)
        existing_position = tag_positions.get(tag)
        if existing_position is not None:
            # Каждый исходный индекс сохраняется при объединении перефразировок.
            source_indexes[existing_position] += indexes
            continue
        tag_positions[tag] = len(candidates)
        candidates.append(_canonical_candidate(tag, observed[tag]))
        source_indexes.append(indexes)

    synthetic_count = 0
    for tag, count in observed_pairs:
        if tag in found_tags:
            continue
        synthetic_count += 1
        candidates.append(_canonical_candidate(tag, count))
        source_indexes.append((selection.generated_count + synthetic_count,))

    result = ViolationCandidateSelection(
        candidates=tuple(candidates),
        source_indexes_by_candidate=tuple(source_indexes),
        generated_count=selection.generated_count + synthetic_count,
        rejected_reasons=tuple(rejected_reasons),
    )
    logger.info(
        "deterministic_duplicate_positions page=%s tags=%s "
        "synthetic=%s normalized=%s raw=%s kept=%s",
        page_number,
        tuple(observed),
        synthetic_count,
        normalized_count,
        result.generated_count,
        result.consolidated_count,
    )
    return RecoveredPositions(
        result, tuple(observed), synthetic_count, normalized_count
    )
