# services/analysis-service/tests/unit/test_technical_assignment_validation.py

"""Unit tests independent T-first validation."""

from typing import Any

import pytest
from pdrd_analysis_service.application.technical_assignment_prompt import (
    build_technical_assignment_check_prompt,
)
from pdrd_analysis_service.application.technical_assignment_schema import (
    build_technical_assignment_check_schema,
)
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
    TechnicalAssignmentValidationError,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
    PageFacts,
)
from pdrd_analysis_service.domain.technical_assignment_validation import (
    TechnicalAssignmentRequirement,
)


def _metrics() -> GenerationMetrics:
    """Создаёт fake generation metrics."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=2600,
        total_duration_ms=10.0,
        load_duration_ms=1.0,
        prompt_eval_count=100,
        eval_count=100,
        content_length=500,
        thinking_length=0,
    )


class _FakeVisionModel:
    """Fake VLM с последовательностью responses."""

    def __init__(
        self,
        responses: list[
            dict[
                str,
                Any,
            ]
        ],
    ) -> None:
        """Сохраняет responses и вызовы."""
        self.responses = list(
            responses,
        )

        self.calls: list[
            dict[
                str,
                Any,
            ]
        ] = []

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[
            str,
            Any,
        ],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает очередной structured response."""
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "num_predict": num_predict,
                "seed": seed,
                "stage": stage,
                "image_bytes": image_bytes,
            }
        )

        return GenerationResult(
            payload=self.responses.pop(
                0,
            ),
            metrics=_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Всегда ready."""
        return True


def _facts() -> PageFacts:
    """Создаёт тестовые page facts."""
    return PageFacts(
        discipline="ЭОМ",
        page_type="scheme",
        summary="Схема электроснабжения",
        objects=("Щит ЩР-1",),
        connections=("ЩР-1 → нагрузка",),
        labels=("QF1",),
        normative_queries=(),
    )


def _requirement(
    index: int,
) -> TechnicalAssignmentRequirement:
    """Создаёт atomic T requirement."""
    return TechnicalAssignmentRequirement(
        point_id=f"point-{index}",
        requirement_id=f"T-R{index}",
        requirement_index=index,
        page=13,
        strength="candidate",
        scopes=("electrical",),
        normative_refs=(),
        source_text=(f"Исходный контекст требования {index}."),
        text=(f"Проект должен выполнить требование {index}."),
    )


def _compact_decision(
    *,
    status: str,
    confidence: float = 0.8,
) -> dict[str, Any]:
    """Создаёт compact T-first decision."""
    return {
        "status": status,
        "confidence": confidence,
    }


def _compact_issue(
    *,
    requirement_id: str,
    status: str,
    severity: str = "warning",
    comment: str = "Результат проверки.",
    evidence: str = "Конкретный факт листа.",
    recommendation: str = "Уточнить проектное решение.",
) -> dict[str, Any]:
    """Создаёт compact issue details."""
    return {
        "requirement_id": requirement_id,
        "status": status,
        "severity": severity,
        "comment": comment,
        "evidence": evidence,
        "recommendation_draft": recommendation,
    }


async def test_first_pass_preserves_all_compact_decisions_and_findings() -> None:
    """Compact response не фильтрует atomic requirements и findings."""
    requirements = tuple(
        _requirement(
            index,
        )
        for index in range(
            1,
            35,
        )
    )

    decisions = {
        requirement.requirement_id: _compact_decision(
            status="satisfied",
        )
        for requirement in requirements
    }

    decisions["T-R2"] = _compact_decision(
        status="violated",
        confidence=0.93,
    )

    decisions["T-R3"] = _compact_decision(
        status="insufficient_evidence",
        confidence=0.71,
    )

    model = _FakeVisionModel(
        [
            {
                "decisions": decisions,
                "issues": [
                    _compact_issue(
                        requirement_id="T-R2",
                        status="violated",
                        severity="error",
                        comment="Требование ТЗ нарушено.",
                        evidence=("На листе указано противоположное решение."),
                        recommendation=("Привести проект к требованию ТЗ."),
                    ),
                    _compact_issue(
                        requirement_id="T-R3",
                        status="insufficient_evidence",
                        comment=(
                            "Выполнение требования ТЗ не подтверждается полностью."
                        ),
                        evidence=("На листе показана только часть требуемого решения."),
                    ),
                ],
            }
        ]
    )

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=model,
        num_predict=2600,
        batch_size=100,
        requirement_text_limit=1800,
    )

    (
        summary,
        parsed_decisions,
        findings,
        metrics,
    ) = await use_case.execute(
        page_number=14,
        page_type="scheme",
        extracted_text="QF1 400 А",
        page_facts=_facts(),
        image_bytes=b"image",
        technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
        analysis_document_id=("22222222-2222-4222-8222-222222222222"),
        section_id=("33333333-3333-4333-8333-333333333333"),
        source_file="ТЗ.pdf",
        source_sha256="a" * 64,
        requirements=requirements,
    )

    assert (
        len(
            model.calls,
        )
        == 1
    )

    assert (
        len(
            parsed_decisions,
        )
        == 34
    )

    assert [decision.requirement_id for decision in parsed_decisions] == [
        requirement.requirement_id for requirement in requirements
    ]

    assert parsed_decisions[0].status == "satisfied"
    assert parsed_decisions[0].severity == "info"
    assert parsed_decisions[0].comment == ""
    assert parsed_decisions[0].evidence == ""

    assert (
        len(
            findings,
        )
        == 2
    )

    violated = findings[0]

    assert violated.finding_id == "p14-tr2"
    assert violated.category == "customer_requirements"
    assert violated.status == "confirmed"
    assert violated.comment == "Требование ТЗ нарушено."
    assert violated.normative_source_ids == ()
    assert violated.basis_sources == ()
    assert violated.technical_assignment_source_ids == ("T-R2",)
    assert violated.technical_assignment_basis_sources[0].text == requirements[1].text

    review = findings[1]

    assert review.finding_id == "p14-tr3"
    assert review.status == "needs_review"

    assert (
        len(
            metrics,
        )
        == 1
    )

    assert "Проверено требований ТЗ: 34" in summary


async def test_default_batch_policy_can_check_96_requirements_in_one_call() -> None:
    """Эталонное ТЗ из 96 requirements требует один VLM call на лист."""
    requirements = tuple(
        _requirement(
            index,
        )
        for index in range(
            1,
            97,
        )
    )

    model = _FakeVisionModel(
        [
            {
                "decisions": {
                    requirement.requirement_id: _compact_decision(
                        status="not_applicable",
                        confidence=0.9,
                    )
                    for requirement in requirements
                },
                "issues": [],
            }
        ]
    )

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=model,
        num_predict=2600,
        batch_size=100,
        requirement_text_limit=1800,
    )

    (
        _summary,
        parsed_decisions,
        findings,
        metrics,
    ) = await use_case.execute(
        page_number=15,
        page_type="plan",
        extracted_text="План электроснабжения.",
        page_facts=_facts(),
        image_bytes=b"image",
        technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
        analysis_document_id=("22222222-2222-4222-8222-222222222222"),
        section_id=("33333333-3333-4333-8333-333333333333"),
        source_file="ТЗ.pdf",
        source_sha256="a" * 64,
        requirements=requirements,
    )

    assert (
        len(
            model.calls,
        )
        == 1
    )
    assert (
        len(
            parsed_decisions,
        )
        == 96
    )
    assert (
        len(
            findings,
        )
        == 0
    )
    assert (
        len(
            metrics,
        )
        == 1
    )
    assert model.calls[0]["stage"] == ("technical_assignment_check:15:1")


async def test_first_pass_fails_if_model_drops_requirement() -> None:
    """Модель не может тихо потерять T-R decision."""
    requirements = (
        _requirement(
            1,
        ),
        _requirement(
            2,
        ),
    )

    model = _FakeVisionModel(
        [
            {
                "decisions": {
                    "T-R1": _compact_decision(
                        status="satisfied",
                    )
                },
                "issues": [],
            }
        ]
    )

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=model,
        num_predict=2600,
        batch_size=100,
        requirement_text_limit=1800,
    )

    with pytest.raises(
        TechnicalAssignmentValidationError,
    ):
        await use_case.execute(
            page_number=1,
            page_type="scheme",
            extracted_text="",
            page_facts=_facts(),
            image_bytes=b"image",
            technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
            analysis_document_id=("22222222-2222-4222-8222-222222222222"),
            section_id=("33333333-3333-4333-8333-333333333333"),
            source_file="ТЗ.pdf",
            source_sha256="a" * 64,
            requirements=requirements,
        )


async def test_first_pass_rejects_duplicate_requirement_ids() -> None:
    """Duplicate T-R IDs не скрываются дедупликацией."""
    requirement = _requirement(
        1,
    )

    model = _FakeVisionModel(
        [],
    )

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=model,
        num_predict=2600,
        batch_size=100,
        requirement_text_limit=1800,
    )

    with pytest.raises(
        ValueError,
    ):
        await use_case.execute(
            page_number=1,
            page_type="scheme",
            extracted_text="",
            page_facts=_facts(),
            image_bytes=b"image",
            technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
            analysis_document_id=("22222222-2222-4222-8222-222222222222"),
            section_id=("33333333-3333-4333-8333-333333333333"),
            source_file="ТЗ.pdf",
            source_sha256="a" * 64,
            requirements=(
                requirement,
                requirement,
            ),
        )

    assert model.calls == []


async def test_compact_finding_requires_issue_details() -> None:
    """Finding status не может потерять evidence в compact формате."""
    requirement = _requirement(
        1,
    )

    model = _FakeVisionModel(
        [
            {
                "decisions": {
                    "T-R1": _compact_decision(
                        status="violated",
                    )
                },
                "issues": [],
            }
        ]
    )

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=model,
        num_predict=2600,
        batch_size=100,
        requirement_text_limit=1800,
    )

    with pytest.raises(
        TechnicalAssignmentValidationError,
        match="issue details",
    ):
        await use_case.execute(
            page_number=1,
            page_type="scheme",
            extracted_text="",
            page_facts=_facts(),
            image_bytes=b"image",
            technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
            analysis_document_id=("22222222-2222-4222-8222-222222222222"),
            section_id=("33333333-3333-4333-8333-333333333333"),
            source_file="ТЗ.pdf",
            source_sha256="a" * 64,
            requirements=(requirement,),
        )


def test_schema_requires_exact_compact_requirement_keys() -> None:
    """Schema требует compact status/confidence по каждому T-R ID."""
    schema = build_technical_assignment_check_schema(
        (
            "T-R1",
            "T-R2",
        )
    )

    decisions = schema["properties"]["decisions"]

    assert decisions["additionalProperties"] is False

    assert decisions["required"] == [
        "T-R1",
        "T-R2",
    ]

    assert set(decisions["properties"]) == {
        "T-R1",
        "T-R2",
    }

    decision = decisions["properties"]["T-R1"]

    assert set(decision["properties"]) == {
        "status",
        "confidence",
    }

    assert decision["required"] == [
        "status",
        "confidence",
    ]

    assert schema["properties"]["issues"]["maxItems"] == 2


def test_prompt_is_high_recall_and_omits_duplicate_source_context() -> None:
    """Prompt сохраняет high-recall и не дублирует source_text."""
    prompt = build_technical_assignment_check_prompt(
        page_number=14,
        extracted_text="QF1 400 А",
        page_facts=_facts(),
        requirements=(
            _requirement(
                1,
            ),
        ),
        requirement_text_limit=1800,
    )

    assert "T-R1" in prompt
    assert "strength=candidate" in prompt
    assert "НЕ ОТБРАСЫВАЙ" in prompt
    assert "not_applicable" in prompt
    assert "satisfied" in prompt
    assert "violated" in prompt
    assert "insufficient_evidence" in prompt
    assert "Не используй ГОСТ" in prompt
    assert "КРИТИЧЕСКИ ВАЖНО ДЛЯ HIGH-RECALL" in prompt
    assert "issues" in prompt
    assert "source_context" not in prompt
    assert "Исходный контекст требования" not in prompt
