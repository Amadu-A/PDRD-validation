# services/analysis-service/tests/unit/test_lossless_findings.py

"""Regression tests finding consolidation и lossless provenance."""

import logging
from typing import Any

import pytest
from pdrd_analysis_service.application.use_cases.common import (
    select_violation_candidates,
)
from pdrd_analysis_service.application.use_cases.normative import (
    CheckPageAgainstNorms,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
    NormativeSource,
    PageFacts,
)


def metrics() -> GenerationMetrics:
    """Возвращает deterministic generation metrics."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=2600,
        total_duration_ms=10.0,
        load_duration_ms=1.0,
        prompt_eval_count=100,
        eval_count=50,
        content_length=500,
        thinking_length=0,
    )


class FakeVisionModel:
    """Возвращает заранее подготовленный JSON."""

    def __init__(
        self,
        payload: dict[str, Any],
    ) -> None:
        """Сохраняет response payload."""
        self._payload = payload

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает deterministic VLM result."""
        assert prompt
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage
        assert image_bytes is not None

        return GenerationResult(
            payload=self._payload,
            metrics=metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def page_facts() -> PageFacts:
    """Возвращает минимальный engineering context."""
    return PageFacts(
        discipline="ЭОМ",
        page_type="scheme",
        summary="Схема электроснабжения",
        objects=("Щит",),
        connections=(),
        labels=(),
        normative_queries=(),
    )


def normative_source(
    source_id: str = "N1",
) -> NormativeSource:
    """Возвращает один разрешённый N-source."""
    return NormativeSource(
        source_id=source_id,
        point_id=f"point-{source_id}",
        score=0.8,
        source_file="PUE.pdf",
        source_path="/norms/PUE.pdf",
        page=10,
        chunk_index=1,
        text="Тестовое нормативное требование.",
    )


def violation(
    *,
    comment: str,
    evidence: str,
    normative_source_ids: list[str] | None = None,
    technical_assignment_source_ids: list[str] | None = None,
    user_package_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Строит candidate finding."""
    return {
        "category": "normative_control",
        "severity": "warning",
        "status": "confirmed",
        "comment": comment,
        "evidence": evidence,
        "recommendation_draft": ("Проверить проектное решение."),
        "confidence": 0.8,
        "normative_source_ids": (
            normative_source_ids if normative_source_ids is not None else []
        ),
        "technical_assignment_source_ids": (
            technical_assignment_source_ids
            if technical_assignment_source_ids is not None
            else []
        ),
        "user_package_source_ids": (
            user_package_source_ids if user_package_source_ids is not None else []
        ),
    }


def test_exact_duplicate_comment_and_evidence_are_consolidated() -> None:
    """Одинаковые comment+evidence становятся одним candidate."""
    candidates = [
        violation(
            comment="Проверить маркировку кабеля.",
            evidence="Участок кабеля не имеет обозначения.",
            normative_source_ids=[
                "N1",
            ],
        ),
        violation(
            comment="  проверить   маркировку кабеля. ",
            evidence=" участок КАБЕЛЯ не имеет обозначения. ",
            normative_source_ids=[
                "N2",
            ],
        ),
    ]

    selection = select_violation_candidates(
        candidates,
    )

    assert selection.generated_count == 2
    assert selection.consolidated_count == 1
    assert selection.represented_count == 2
    assert selection.preserved_count == 2
    assert selection.duplicate_count == 1
    assert selection.rejected_count == 0

    assert selection.source_indexes_by_candidate == (
        (
            1,
            2,
        ),
    )

    assert (
        len(
            selection.candidates,
        )
        == 1
    )

    assert selection.candidates[0]["normative_source_ids"] == [
        "N1",
        "N2",
    ]

    assert selection.candidates[0]["comment"] == ("Проверить маркировку кабеля.")


def test_exact_duplicate_merges_all_source_id_namespaces() -> None:
    """Exact duplicate сохраняет union N/T/U source IDs."""
    candidates = [
        violation(
            comment="Обнаружено несоответствие.",
            evidence="Один и тот же наблюдаемый факт.",
            normative_source_ids=[
                "N1",
            ],
            technical_assignment_source_ids=[
                "T1",
            ],
            user_package_source_ids=[
                "U1",
            ],
        ),
        violation(
            comment="Обнаружено несоответствие.",
            evidence="Один и тот же наблюдаемый факт.",
            normative_source_ids=[
                "N1",
                "N2",
            ],
            technical_assignment_source_ids=[
                "T2",
            ],
            user_package_source_ids=[
                "U1",
                "U2",
            ],
        ),
    ]

    selection = select_violation_candidates(
        candidates,
    )

    assert selection.consolidated_count == 1

    candidate = selection.candidates[0]

    assert candidate["normative_source_ids"] == [
        "N1",
        "N2",
    ]

    assert candidate["technical_assignment_source_ids"] == [
        "T1",
        "T2",
    ]

    assert candidate["user_package_source_ids"] == [
        "U1",
        "U2",
    ]


def test_same_comment_with_different_evidence_stays_distinct() -> None:
    """Одинаковый текст замечания не склеивает разные физические факты."""
    candidates = [
        violation(
            comment="Проверить маркировку кабеля.",
            evidence="Первый участок кабеля.",
        ),
        violation(
            comment="Проверить маркировку кабеля.",
            evidence="Другой участок кабеля.",
        ),
    ]

    selection = select_violation_candidates(
        candidates,
    )

    assert selection.generated_count == 2
    assert selection.consolidated_count == 2
    assert selection.represented_count == 2
    assert selection.duplicate_count == 0

    assert selection.source_indexes_by_candidate == (
        (1,),
        (2,),
    )


def test_semantic_content_is_not_filtered_after_generation() -> None:
    """Semantic correctness пока не является частью exact-dedupe этапа."""
    candidate = violation(
        comment=("Решение соответствует требованиям."),
        evidence=("На листе требование выполнено."),
    )

    selection = select_violation_candidates(
        [
            candidate,
        ]
    )

    assert selection.generated_count == 1
    assert selection.consolidated_count == 1
    assert selection.preserved_count == 1
    assert selection.rejected_count == 0
    assert selection.rejection_counts == {}
    assert selection.candidates == (candidate,)


def test_blank_text_candidates_are_not_consolidated() -> None:
    """Недостаточно описанные candidates не склеиваются без доказательства."""
    candidates = [
        violation(
            comment="",
            evidence="",
        ),
        violation(
            comment="",
            evidence="",
        ),
    ]

    selection = select_violation_candidates(
        candidates,
    )

    assert selection.generated_count == 2
    assert selection.consolidated_count == 2
    assert selection.represented_count == 2
    assert selection.duplicate_count == 0
    assert selection.rejected_count == 0

    assert selection.source_indexes_by_candidate == (
        (1,),
        (2,),
    )


def test_malformed_candidate_collection_fails_instead_of_losing_data() -> None:
    """Невалидная структура не превращается в тихое уменьшение findings."""
    with pytest.raises(
        ValueError,
        match="JSON array",
    ):
        select_violation_candidates(
            {
                "comment": "not-a-list",
            }
        )

    with pytest.raises(
        ValueError,
        match="JSON object",
    ):
        select_violation_candidates(
            [
                "not-an-object",
            ]
        )


async def test_exact_duplicates_become_one_finding_before_downstream_stages(
    caplog: Any,
) -> None:
    """Exact duplicates дают один FindingDraft и auditable provenance log."""
    caplog.set_level(
        logging.INFO,
    )

    model = FakeVisionModel(
        {
            "summary": "Есть повторяющееся замечание.",
            "violations": [
                violation(
                    comment="Отсутствует маркировка.",
                    evidence="Кабель X1 не имеет маркировки.",
                    normative_source_ids=[
                        "N1",
                    ],
                ),
                violation(
                    comment=" отсутствует   маркировка. ",
                    evidence=" кабель x1 НЕ имеет маркировки. ",
                    normative_source_ids=[
                        "N1",
                    ],
                ),
            ],
        }
    )

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=2600,
        max_issues=10,
        normative_text_limit=700,
    )

    _, findings, _ = await use_case.execute(
        page_number=16,
        extracted_text="Тестовый лист.",
        page_facts=page_facts(),
        normative_sources=(normative_source(),),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 1
    )

    finding = findings[0]

    assert finding.finding_id == "p16-f1"

    assert finding.comment == "Отсутствует маркировка."
    assert finding.evidence == "Кабель X1 не имеет маркировки."

    assert "generated=2 consolidated=1" in caplog.text
    assert "duplicates=1 represented=2" in caplog.text

    assert "normative_candidate_exact_duplicates_consolidated" in caplog.text

    assert "raw_candidate_indexes=(1, 2)" in caplog.text
    assert "provenance_lossless=True" in caplog.text


async def test_unknown_source_id_does_not_delete_candidate(
    caplog: Any,
) -> None:
    """Hallucinated source-id отсоединяется, finding сохраняется."""
    caplog.set_level(
        logging.INFO,
    )

    model = FakeVisionModel(
        {
            "summary": "Есть замечание.",
            "violations": [
                violation(
                    comment=("На листе обнаружено подозрительное решение."),
                    evidence=("Параметры элементов требуют проверки."),
                    normative_source_ids=[
                        "N999",
                    ],
                )
            ],
        }
    )

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=2600,
        max_issues=10,
        normative_text_limit=700,
    )

    _, findings, _ = await use_case.execute(
        page_number=14,
        extracted_text="Тестовый лист.",
        page_facts=page_facts(),
        normative_sources=(normative_source(),),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 1
    )

    finding = findings[0]

    assert finding.comment == ("На листе обнаружено подозрительное решение.")

    assert finding.normative_source_ids == ()
    assert finding.basis_sources == ()
    assert finding.basis == ""

    assert finding.category == "other"
    assert finding.status == "needs_review"

    assert "normative_candidate_source_ids_detached" in caplog.text

    assert "finding_id=p14-f1" in caplog.text

    assert "N999" in caplog.text

    assert "generated=1 consolidated=1" in caplog.text
    assert "duplicates=0 represented=1" in caplog.text
    assert "provenance_lossless=True" in caplog.text


async def test_invalid_source_is_detached_but_valid_source_is_kept(
    caplog: Any,
) -> None:
    """Частично ошибочный source list не уничтожает valid evidence."""
    caplog.set_level(
        logging.INFO,
    )

    model = FakeVisionModel(
        {
            "summary": "Есть замечание.",
            "violations": [
                violation(
                    comment=("На листе выявлено нормативное несоответствие."),
                    evidence=("Видимый факт противоречит переданному требованию."),
                    normative_source_ids=[
                        "N1",
                        "N999",
                    ],
                )
            ],
        }
    )

    use_case = CheckPageAgainstNorms(
        vision_model=model,
        num_predict=2600,
        max_issues=10,
        normative_text_limit=700,
    )

    _, findings, _ = await use_case.execute(
        page_number=15,
        extracted_text="Тестовый лист.",
        page_facts=page_facts(),
        normative_sources=(normative_source(),),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 1
    )

    finding = findings[0]

    assert finding.normative_source_ids == ("N1",)

    assert (
        len(
            finding.basis_sources,
        )
        == 1
    )

    assert finding.category == "normative_control"
    assert finding.status == "confirmed"

    assert "N999" in caplog.text

    assert "raw_candidates=1 findings=1" in caplog.text
    assert "duplicates=0 represented=1" in caplog.text
    assert "provenance_lossless=True" in caplog.text
