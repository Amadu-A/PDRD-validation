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

_CANDIDATE_SOURCE_ID_FIELDS = (
    "normative_source_ids",
    "technical_assignment_source_ids",
    "user_package_source_ids",
)

_STRUCTURED_NUMBER_ANCHOR_RE = re.compile(
    r"(?<![\w])"
    r"\d+(?:\.\d+){1,4}"
    r"(?:\s*[-–—]\s*\d+(?:\.\d+){1,4})?"
    r"(?![\w])",
)

_TAG_ANCHOR_RE = re.compile(
    r"(?<![\w])"
    r"([A-ZА-ЯЁ]{1,4}\d+(?:\.\d+)*)"
    r"(?![\w])",
)

_ISSUE_SIGNATURE_PATTERNS = (
    (
        "missing",
        re.compile(
            r"(?:"
            r"\bотсутств\w*"
            r"|\bне\s+указ\w*"
            r"|\bне\s+заполн\w*"
            r"|\bпуст\w*"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "duplicate",
        re.compile(
            r"(?:\bдублир\w*|\bповтор\w*)",
            re.IGNORECASE,
        ),
    ),
    (
        "mismatch",
        re.compile(
            r"(?:"
            r"\bнесоответ\w*"
            r"|\bрасхожд\w*"
            r"|\bпротивореч\w*"
            r"|\bне\s+совпад\w*"
            r")",
            re.IGNORECASE,
        ),
    ),
)

_PROPERTY_SIGNATURE_PATTERNS = (
    (
        "position",
        re.compile(
            r"(?:\bпозици\w*|\bпозицион\w*)",
            re.IGNORECASE,
        ),
    ),
    (
        "marking",
        re.compile(
            r"\bмаркиров\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "type_mark",
        re.compile(
            r"(?:"
            r"\bтип\w*"
            r"|\bмарка\b"
            r"|\bмарки\b"
            r"|\bмарке\b"
            r"|\bмарку\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "code",
        re.compile(
            r"\bкод\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "manufacturer",
        re.compile(
            r"(?:"
            r"\bзавод\w*"
            r"|\bизготов\w*"
            r"|\bпроизводител\w*"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "mass",
        re.compile(
            r"\bмасс\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "quantity",
        re.compile(
            r"\bколичеств\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "designation",
        re.compile(
            r"\bобозначен\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "power",
        re.compile(
            r"\bмощност\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "flow",
        re.compile(
            r"\bрасход\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "pressure",
        re.compile(
            r"(?:\bдавлен\w*|\bперепад\w*|ΔР)",
            re.IGNORECASE,
        ),
    ),
    (
        "temperature",
        re.compile(
            r"\bтемператур\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "diameter",
        re.compile(
            r"\bдиаметр\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "volume",
        re.compile(
            r"(?:"
            r"\bобъем\w*"
            r"|\bобъём\w*"
            r"|\bемкост\w*"
            r"|\bёмкост\w*"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "sheet_number",
        re.compile(
            r"(?:\bномер\w*\s+лист\w*|\bлист\w*\s+\d+)",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ViolationCandidateSelection:
    """Результат consolidation generated finding candidates."""

    candidates: tuple[
        dict[str, Any],
        ...,
    ]

    source_indexes_by_candidate: tuple[
        tuple[
            int,
            ...,
        ],
        ...,
    ]

    generated_count: int

    rejected_reasons: tuple[
        str,
        ...,
    ] = ()

    @property
    def consolidated_count(
        self,
    ) -> int:
        """Возвращает количество distinct candidates после safe dedupe."""
        return len(
            self.candidates,
        )

    @property
    def represented_count(
        self,
    ) -> int:
        """Возвращает количество raw candidates, сохранённых в provenance."""
        return sum(
            len(
                source_indexes,
            )
            for source_indexes in self.source_indexes_by_candidate
        )

    @property
    def duplicate_count(
        self,
    ) -> int:
        """Возвращает количество объединённых duplicate candidates."""
        return max(
            self.represented_count - self.consolidated_count,
            0,
        )

    @property
    def preserved_count(
        self,
    ) -> int:
        """Backward-compatible число raw candidates, сохранённых в provenance."""
        return self.represented_count

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
    """Нормализует строку для deterministic comparison."""
    return re.sub(
        r"\s+",
        " ",
        str(
            value or "",
        ).lower(),
    ).strip()


def _candidate_identity(
    candidate: dict[
        str,
        Any,
    ],
) -> (
    tuple[
        str,
        str,
    ]
    | None
):
    """Возвращает безопасный exact-dedupe key candidate."""
    comment = normalize_text(
        candidate.get(
            "comment",
        )
    )

    evidence = normalize_text(
        candidate.get(
            "evidence",
        )
    )

    if not comment or not evidence:
        return None

    return (
        comment,
        evidence,
    )


def _candidate_anchor_signature(
    candidate: dict[
        str,
        Any,
    ],
) -> tuple[
    str,
    ...,
]:
    """Извлекает явные object/position anchors из comment и evidence."""
    text = " ".join(
        (
            str(
                candidate.get(
                    "comment",
                    "",
                )
            ),
            str(
                candidate.get(
                    "evidence",
                    "",
                )
            ),
        )
    )

    anchors: set[str] = set()

    for raw_anchor in _STRUCTURED_NUMBER_ANCHOR_RE.findall(
        text,
    ):
        anchors.add(
            re.sub(
                r"\s*[–—]\s*",
                "-",
                raw_anchor,
            )
        )

    for raw_anchor in _TAG_ANCHOR_RE.findall(
        text,
    ):
        normalized = raw_anchor.upper()

        if re.fullmatch(
            r"[NTUE]\d+",
            normalized,
        ):
            continue

        if normalized.startswith(
            (
                "СП",
                "ГОСТ",
                "ПУЭ",
            )
        ):
            continue

        anchors.add(
            normalized,
        )

    return tuple(
        sorted(
            anchors,
        )
    )


def _candidate_issue_signature(
    candidate: dict[
        str,
        Any,
    ],
) -> str | None:
    """Классифицирует только безопасные повторяющиеся issue patterns."""
    text = " ".join(
        (
            str(
                candidate.get(
                    "comment",
                    "",
                )
            ),
            str(
                candidate.get(
                    "evidence",
                    "",
                )
            ),
        )
    )

    for (
        signature,
        pattern,
    ) in _ISSUE_SIGNATURE_PATTERNS:
        if pattern.search(
            text,
        ):
            return signature

    return None


def _candidate_property_signature(
    candidate: dict[
        str,
        Any,
    ],
) -> tuple[
    str,
    ...,
]:
    """Извлекает проверяемые свойства для защиты от false merge."""
    text = " ".join(
        (
            str(
                candidate.get(
                    "comment",
                    "",
                )
            ),
            str(
                candidate.get(
                    "evidence",
                    "",
                )
            ),
        )
    )

    result = tuple(
        signature
        for (
            signature,
            pattern,
        ) in _PROPERTY_SIGNATURE_PATTERNS
        if pattern.search(
            text,
        )
    )

    return result


def _candidate_structural_identity(
    candidate: dict[
        str,
        Any,
    ],
) -> (
    tuple[
        str,
        str,
        tuple[str, ...],
        tuple[str, ...],
    ]
    | None
):
    """Возвращает conservative object-aware duplicate identity.

    Structural identity используется только для повтора одной позиции,
    когда известны machine category, явный anchor и свойство. Сравнения
    нескольких номеров могут относиться к разным физическим объектам.
    """
    category_value = normalize_text(
        candidate.get(
            "category",
        )
    )

    anchors = _candidate_anchor_signature(
        candidate,
    )

    issue_signature = _candidate_issue_signature(
        candidate,
    )

    property_signature = _candidate_property_signature(
        candidate,
    )

    # Общие номера и тип проблемы не доказывают, что два сравнительных
    # замечания относятся к одному физическому объекту. Нечёткое объединение
    # оставляем только для повтора одной позиционной подписи; остальные
    # кандидаты объединяются лишь при точном совпадении comment и evidence.
    if (
        issue_signature != "duplicate"
        or "position" not in property_signature
        or len(anchors) != 1
    ):
        return None

    if (
        not category_value
        or not anchors
        or issue_signature is None
        or not property_signature
    ):
        return None

    return (
        category_value,
        issue_signature,
        anchors,
        property_signature,
    )


def _source_id_list(
    value: Any,
) -> list[str]:
    """Возвращает уникальные source IDs с сохранением порядка."""
    if not isinstance(
        value,
        list,
    ):
        return []

    result: list[str] = []
    seen: set[str] = set()

    for item in value:
        source_id = str(
            item,
        ).strip()

        if not source_id or source_id in seen:
            continue

        seen.add(
            source_id,
        )

        result.append(
            source_id,
        )

    return result


def _merge_candidate_source_ids(
    *,
    target: dict[
        str,
        Any,
    ],
    duplicate: dict[
        str,
        Any,
    ],
) -> None:
    """Объединяет N/T/U source IDs duplicate candidates."""
    for field_name in _CANDIDATE_SOURCE_ID_FIELDS:
        merged: list[str] = []
        seen: set[str] = set()

        for source_id in (
            *_source_id_list(
                target.get(
                    field_name,
                )
            ),
            *_source_id_list(
                duplicate.get(
                    field_name,
                )
            ),
        ):
            if source_id in seen:
                continue

            seen.add(
                source_id,
            )

            merged.append(
                source_id,
            )

        if merged:
            target[field_name] = merged


def select_violation_candidates(
    violations: Any,
) -> ViolationCandidateSelection:
    """Выполняет lossless deterministic candidate consolidation.

    Сначала объединяются exact duplicates, когда после базовой
    нормализации совпадают одновременно comment и evidence.

    Дополнительно допускается conservative consolidation для повтора
    одной и той же позиционной подписи с одинаковыми category и свойством.

    Разные anchors никогда не объединяются этим правилом.
    Candidates без достаточной structural identity остаются distinct.

    Для каждой итоговой записи сохраняются 1-based индексы всех
    исходных candidates, поэтому consolidation остаётся auditable.
    """
    if violations is None:
        return ViolationCandidateSelection(
            candidates=(),
            source_indexes_by_candidate=(),
            generated_count=0,
        )

    if not isinstance(
        violations,
        list,
    ):
        raise ValueError(
            "Поле violations должно быть JSON array.",
        )

    candidates: list[
        dict[
            str,
            Any,
        ]
    ] = []

    source_indexes_by_candidate: list[list[int]] = []

    candidate_position_by_identity: dict[
        tuple[
            str,
            str,
        ],
        int,
    ] = {}

    candidate_position_by_structural_identity: dict[
        tuple[
            str,
            str,
            tuple[str, ...],
            tuple[str, ...],
        ],
        int,
    ] = {}

    for raw_index, violation in enumerate(
        violations,
        start=1,
    ):
        if not isinstance(
            violation,
            dict,
        ):
            raise ValueError(
                "Каждый элемент violations должен быть JSON object; "
                f"index={raw_index - 1}.",
            )

        candidate = dict(
            violation,
        )

        identity = _candidate_identity(
            candidate,
        )

        structural_identity = (
            _candidate_structural_identity(
                candidate,
            )
            if identity is not None
            else None
        )

        existing_position = (
            candidate_position_by_identity.get(
                identity,
            )
            if identity is not None
            else None
        )

        if existing_position is None and structural_identity is not None:
            existing_position = candidate_position_by_structural_identity.get(
                structural_identity,
            )

        if existing_position is not None:
            _merge_candidate_source_ids(
                target=candidates[existing_position],
                duplicate=candidate,
            )

            source_indexes_by_candidate[existing_position].append(
                raw_index,
            )

            if identity is not None:
                candidate_position_by_identity[identity] = existing_position

            if structural_identity is not None:
                candidate_position_by_structural_identity[structural_identity] = (
                    existing_position
                )

            continue

        candidate_position = len(
            candidates,
        )

        candidates.append(
            candidate,
        )

        source_indexes_by_candidate.append(
            [
                raw_index,
            ]
        )

        if identity is not None:
            candidate_position_by_identity[identity] = candidate_position

        if structural_identity is not None:
            candidate_position_by_structural_identity[structural_identity] = (
                candidate_position
            )

    return ViolationCandidateSelection(
        candidates=tuple(
            candidates,
        ),
        source_indexes_by_candidate=tuple(
            tuple(
                source_indexes,
            )
            for source_indexes in source_indexes_by_candidate
        ),
        generated_count=len(
            violations,
        ),
    )


def filter_violations(
    violations: Any,
) -> list[dict[str, Any]]:
    """Возвращает backward-compatible consolidated список candidates."""
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
