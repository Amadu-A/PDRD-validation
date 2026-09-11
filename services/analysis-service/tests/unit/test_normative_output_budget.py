# services/analysis-service/tests/unit/test_normative_output_budget.py

"""Unit tests compact high-recall normative output contract."""

from pdrd_analysis_service.application.json_schemas import (
    build_normative_check_schema,
)
from pdrd_analysis_service.application.use_cases.common import (
    select_violation_candidates,
)
from pdrd_analysis_service.application.use_cases.normative import (
    _HIGH_RECALL_FINDING_POLICY,
)

_SYNTHETIC_CANDIDATE_COUNT = 1000


def _synthetic_candidate(
    index: int,
) -> dict[str, object]:
    """Создаёт schema-shaped fabricated finding candidate."""
    return {
        "category": "marking",
        "severity": "warning",
        "status": "needs_review",
        "comment": (f"Краткое замечание {index}."),
        "evidence": (f"Краткий видимый факт {index}."),
        "recommendation_draft": "",
        "confidence": 0.75,
        "normative_source_ids": [],
        "technical_assignment_source_ids": [],
        "user_package_source_ids": [],
    }


def test_normative_schema_keeps_fifty_candidate_capacity() -> None:
    """Компактизация текста не уменьшает число разрешённых findings."""
    schema = build_normative_check_schema(
        source_ids=(),
        max_issues=50,
    )

    violations = schema["properties"]["violations"]

    finding = violations["items"]

    properties = finding["properties"]

    assert violations["maxItems"] == 50

    assert schema["properties"]["summary"]["maxLength"] == 200

    assert properties["comment"]["maxLength"] == 160

    assert properties["evidence"]["maxLength"] == 180

    assert properties["recommendation_draft"]["maxLength"] == 0

    assert "recommendation_draft" in finding["required"]


def test_high_recall_prompt_forbids_count_reduction_for_compaction() -> None:
    """Prompt сокращает текст, а не количество generated candidates."""
    assert "НЕ сокращай количество candidate findings" in _HIGH_RECALL_FINDING_POLICY

    assert "comment: не более 160" in _HIGH_RECALL_FINDING_POLICY

    assert "evidence: не более 180" in _HIGH_RECALL_FINDING_POLICY

    assert 'recommendation_draft=""' in _HIGH_RECALL_FINDING_POLICY


def test_lossless_selector_preserves_synthetic_candidate_load() -> None:
    """Synthetic load подтверждает отсутствие скрытого backend cap/filter."""
    candidates = [
        _synthetic_candidate(
            index,
        )
        for index in range(
            _SYNTHETIC_CANDIDATE_COUNT,
        )
    ]

    selection = select_violation_candidates(
        candidates,
    )

    assert selection.generated_count == _SYNTHETIC_CANDIDATE_COUNT

    assert selection.preserved_count == _SYNTHETIC_CANDIDATE_COUNT

    assert selection.rejected_count == 0

    assert selection.candidates[0]["comment"] == "Краткое замечание 0."

    assert selection.candidates[-1]["comment"] == "Краткое замечание 999."
