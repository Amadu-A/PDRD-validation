# services/analysis-service/src/pdrd_analysis_service/application/use_cases/common.py

"""Общие pure helpers Analysis application use cases."""

import re
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ViolationCandidateSelection:
    """Результат lossless передачи generated finding candidates."""

    candidates: tuple[
        dict[str, Any],
        ...,
    ]

    generated_count: int

    rejected_reasons: tuple[
        str,
        ...,
    ] = ()

    @property
    def preserved_count(
        self,
    ) -> int:
        """Возвращает количество сохранённых candidates."""
        return len(
            self.candidates,
        )

    @property
    def rejected_count(
        self,
    ) -> int:
        """Возвращает количество structural rejection."""
        return len(
            self.rejected_reasons,
        )

    @property
    def rejection_counts(
        self,
    ) -> dict[
        str,
        int,
    ]:
        """Группирует диагностические причины structural rejection."""
        result: dict[
            str,
            int,
        ] = {}

        for reason in self.rejected_reasons:
            result[reason] = (
                result.get(
                    reason,
                    0,
                )
                + 1
            )

        return result


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
    """Нормализует строку для comparison без semantic filtering."""
    return re.sub(
        r"\s+",
        " ",
        str(
            value or "",
        ).lower(),
    ).strip()


def select_violation_candidates(
    violations: Any,
) -> ViolationCandidateSelection:
    """Передаёт каждый schema-valid generated candidate без semantic filtering."""
    if violations is None:
        return ViolationCandidateSelection(
            candidates=(),
            generated_count=0,
        )

    if not isinstance(
        violations,
        list,
    ):
        raise ValueError(
            "Поле violations должно быть JSON array.",
        )

    candidates: list[dict[str, Any]] = []

    for index, violation in enumerate(
        violations,
    ):
        if not isinstance(
            violation,
            dict,
        ):
            raise ValueError(
                f"Каждый элемент violations должен быть JSON object; index={index}.",
            )

        candidates.append(
            violation,
        )

    return ViolationCandidateSelection(
        candidates=tuple(
            candidates,
        ),
        generated_count=len(
            violations,
        ),
    )


def filter_violations(
    violations: Any,
) -> list[dict[str, Any]]:
    """Возвращает backward-compatible lossless список candidates."""
    return list(
        select_violation_candidates(
            violations,
        ).candidates
    )


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
            parts.append(
                f"{source.source_file}, PDF стр. {source.page}",
            )

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
