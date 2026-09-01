# services/analysis-service/src/pdrd_analysis_service/application/use_cases/common.py

"""Общие pure helpers Analysis application use cases."""

import re
from typing import Any

from pdrd_analysis_service.application.json_schemas import (
    FINDING_CATEGORIES,
    FINDING_SEVERITIES,
    FINDING_STATUSES,
)
from pdrd_analysis_service.domain.analysis import (
    FindingCategory,
    FindingSeverity,
    FindingStatus,
    GenerationMetrics,
    NormativeSource,
)

_POSITIVE_COMPLIANCE_MARKERS = (
    "соответствует требован",
    "соответствует рекоменда",
    "соответствует норм",
    "выполнено в соответствии",
    "выполнено правильно",
    "требование выполнено",
    "требования выполнены",
    "требование соблюдено",
    "требования соблюдены",
    "нарушений не выявлено",
    "нарушение отсутствует",
    "как это сделано на листе",
    "что соответствует",
)

_NEGATIVE_VIOLATION_MARKERS = (
    "не соответствует",
    "не выполн",
    "не указан",
    "не указана",
    "не указаны",
    "не соблюд",
    "отсутств",
    "противореч",
    "недостаточ",
    "ошиб",
    "невер",
    "некоррект",
    "требуется исправ",
    "необходимо исправ",
    "необходимо добавить",
    "требуется добавить",
    "нарушено",
    "нарушены",
    "выявлено нарушение",
    "выявлены нарушения",
)


def zero_metrics(
    reason: str,
) -> GenerationMetrics:
    """Возвращает пустые метрики этапа без VLM-вызова."""
    return GenerationMetrics(
        attempt=0,
        done_reason=reason,
        requested_num_predict=0,
        total_duration_ms=0.0,
        load_duration_ms=0.0,
        prompt_eval_count=0,
        eval_count=0,
        content_length=0,
        thinking_length=0,
    )


def string_tuple(
    value: Any,
    *,
    limit: int,
) -> tuple[str, ...]:
    """Нормализует список строк из VLM JSON."""
    if not isinstance(
        value,
        list,
    ):
        return ()

    result: list[str] = []

    for item in value:
        normalized = str(
            item,
        ).strip()

        if not normalized:
            continue

        result.append(
            normalized,
        )

        if len(result) >= limit:
            break

    return tuple(
        result,
    )


def normalize_text(
    value: Any,
) -> str:
    """Нормализует строку для comparison/filtering."""
    return re.sub(
        r"\s+",
        " ",
        str(
            value or "",
        ).lower(),
    ).strip()


def filter_violations(
    violations: Any,
) -> list[dict[str, Any]]:
    """Удаляет compliance confirmations и дубли."""
    if not isinstance(
        violations,
        list,
    ):
        return []

    result: list[dict[str, Any]] = []

    seen: set[str] = set()

    for violation in violations:
        if not isinstance(
            violation,
            dict,
        ):
            continue

        combined = " ".join(
            (
                normalize_text(
                    violation.get(
                        "comment",
                    )
                ),
                normalize_text(
                    violation.get(
                        "evidence",
                    )
                ),
                normalize_text(
                    violation.get(
                        "recommendation_draft",
                    )
                ),
            )
        )

        has_positive = any(
            marker in combined for marker in _POSITIVE_COMPLIANCE_MARKERS
        )

        has_negative = any(marker in combined for marker in _NEGATIVE_VIOLATION_MARKERS)

        if has_positive and not has_negative:
            continue

        comment = normalize_text(
            violation.get(
                "comment",
            )
        )

        if not comment:
            continue

        dedupe_key = re.sub(
            r"[^a-zа-яё0-9]+",
            " ",
            comment,
            flags=re.IGNORECASE,
        ).strip()

        if dedupe_key in seen:
            continue

        seen.add(
            dedupe_key,
        )

        result.append(
            violation,
        )

    return result


def build_basis(
    sources: tuple[
        NormativeSource,
        ...,
    ],
) -> str:
    """Формирует нормативное основание."""
    parts: list[str] = []

    for source in sources:
        if not source.source_file:
            continue

        if source.page is None:
            parts.append(
                source.source_file,
            )
        else:
            parts.append(f"{source.source_file}, PDF стр. {source.page}")

    return "; ".join(
        parts,
    )


def category(
    value: Any,
) -> FindingCategory:
    """Нормализует finding category."""
    normalized = str(
        value,
    )

    if normalized in FINDING_CATEGORIES:
        return normalized  # type: ignore[return-value]

    return "other"


def severity(
    value: Any,
) -> FindingSeverity:
    """Нормализует finding severity."""
    normalized = str(
        value,
    )

    if normalized in FINDING_SEVERITIES:
        return normalized  # type: ignore[return-value]

    return "warning"


def finding_status(
    value: Any,
) -> FindingStatus:
    """Нормализует finding status."""
    normalized = str(
        value,
    )

    if normalized in FINDING_STATUSES:
        return normalized  # type: ignore[return-value]

    return "needs_review"


def confidence(
    value: Any,
) -> float:
    """Нормализует confidence в диапазон 0..1."""
    try:
        result = float(
            value,
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0

    return min(
        max(
            result,
            0.0,
        ),
        1.0,
    )
