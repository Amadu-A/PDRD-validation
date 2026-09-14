# services/analysis-service/tests/unit/test_lossless_findings.py

"""Regression tests lossless findings pipeline."""

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


def normative_source() -> NormativeSource:
    """Возвращает один разрешённый N-source."""
    return NormativeSource(
        source_id="N1",
        point_id="point-1",
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
        "technical_assignment_source_ids": [],
        "user_package_source_ids": [],
    }


def test_exact_duplicate_comments_are_not_removed_by_python() -> None:
    """Semantic dedupe оставляется будущему feedback filter."""
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
    assert selection.preserved_count == 2
    assert selection.rejected_count == 0

    assert (
        len(
            selection.candidates,
        )
        == 2
    )


def test_semantic_content_is_not_filtered_after_generation() -> None:
    """Даже ошибочно generated compliance candidate не исчезает молча."""
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
    assert selection.preserved_count == 1
    assert selection.rejected_count == 0
    assert selection.rejection_counts == {}
    assert selection.candidates == (candidate,)


def test_blank_text_is_not_silently_deleted_by_post_filter() -> None:
    """Структурную ошибку должен ловить schema boundary, не semantic filter."""
    candidate = violation(
        comment="",
        evidence="",
    )

    selection = select_violation_candidates(
        [
            candidate,
        ]
    )

    assert selection.generated_count == 1
    assert selection.preserved_count == 1
    assert selection.rejected_count == 0


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

    assert "N999" in caplog.text

    assert "generated=1 preserved=1 rejected=0" in caplog.text
    assert "lossless=true" in caplog.text


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

    assert "candidates=1 findings=1" in caplog.text
