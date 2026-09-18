# services/analysis-service/tests/unit/test_technical_assignment_status_source.py

"""Tests single source of truth для T-first status."""

from typing import Any

from pdrd_analysis_service.application.technical_assignment_prompt import (
    build_technical_assignment_check_prompt,
)
from pdrd_analysis_service.application.technical_assignment_schema import (
    build_technical_assignment_check_schema,
)
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
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
    """Возвращает deterministic fake metrics."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=4000,
        total_duration_ms=10.0,
        load_duration_ms=1.0,
        prompt_eval_count=100,
        eval_count=50,
        content_length=300,
        thinking_length=0,
    )


def _facts() -> PageFacts:
    """Возвращает page facts принципиальной схемы."""
    return PageFacts(
        discipline="Тепломеханические решения",
        page_type="Принципиальная схема котельной",
        summary="Принципиальная схема котельной.",
        objects=(
            "Котёл",
            "Циркуляционный насос",
        ),
        connections=("Котёл → тепловая сеть",),
        labels=(
            "PG",
            "TG",
        ),
        normative_queries=(),
    )


def _requirement() -> TechnicalAssignmentRequirement:
    """Возвращает одно atomic требование ТЗ."""
    return TechnicalAssignmentRequirement(
        point_id="point-1",
        requirement_id="T-R1",
        requirement_index=1,
        page=4,
        strength="explicit",
        scopes=("heating",),
        normative_refs=(),
        source_text=("Техническое требование к оборудованию."),
        text=("На схеме должен быть предусмотрен необходимый элемент."),
    )


class _VisionModel:
    """Fake VLM с заданным structured payload."""

    def __init__(
        self,
        payload: dict[str, Any],
    ) -> None:
        """Сохраняет payload."""
        self.payload = payload

        self.calls = 0

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
        """Возвращает configured structured response."""
        assert prompt
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage.startswith(
            "technical_assignment_check:",
        )
        assert image_bytes

        self.calls += 1

        return GenerationResult(
            payload=self.payload,
            metrics=_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


async def _execute(
    payload: dict[str, Any],
):
    """Запускает публичный T-first use case."""
    model = _VisionModel(
        payload,
    )

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=model,
        num_predict=4000,
        batch_size=48,
        requirement_text_limit=1800,
    )

    result = await use_case.execute(
        page_number=19,
        page_type="Принципиальная схема котельной",
        extracted_text="Котёл. Насос. PG. TG.",
        page_facts=_facts(),
        image_bytes=b"image",
        technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
        analysis_document_id=("22222222-2222-4222-8222-222222222222"),
        section_id=("33333333-3333-4333-8333-333333333333"),
        source_file="ТЗ.docx",
        source_sha256="a" * 64,
        requirements=(_requirement(),),
    )

    return (
        model,
        result,
    )


def test_issue_schema_does_not_duplicate_decision_status() -> None:
    """Issue schema хранит details, но не второй generated status."""
    schema = build_technical_assignment_check_schema(("T-R1",))

    issue_schema = schema["properties"]["issues"]["items"]

    properties = issue_schema["properties"]

    required = issue_schema["required"]

    assert "status" not in properties
    assert "status" not in required

    assert "requirement_id" in properties
    assert "severity" in properties
    assert "comment" in properties
    assert "evidence" in properties
    assert "recommendation_draft" in properties


def test_prompt_declares_decision_status_single_source_of_truth() -> None:
    """Prompt запрещает redundant status внутри issue."""
    prompt = build_technical_assignment_check_prompt(
        page_number=19,
        extracted_text="Котёл. Насос.",
        page_facts=_facts(),
        requirements=(_requirement(),),
        requirement_text_limit=1800,
    )

    assert "ЕДИНСТВЕННЫМ источником итогового статуса" in prompt

    assert "status в issues НЕ возвращай" in prompt


async def test_issue_without_status_uses_decision_status() -> None:
    """Schema-conformant issue строит finding по authoritative decision."""
    model, result = await _execute(
        {
            "decisions": {
                "T-R1": {
                    "status": "violated",
                    "confidence": 0.93,
                },
            },
            "issues": [
                {
                    "requirement_id": "T-R1",
                    "severity": "error",
                    "comment": ("Требование ТЗ нарушено."),
                    "evidence": ("На листе показано противоположное решение."),
                    "recommendation_draft": ("Привести решение в соответствие с ТЗ."),
                },
            ],
        }
    )

    (
        _summary,
        decisions,
        findings,
        _metrics_result,
    ) = result

    assert model.calls == 1

    assert (
        len(
            decisions,
        )
        == 1
    )

    assert decisions[0].status == "violated"

    assert (
        len(
            findings,
        )
        == 1
    )

    assert findings[0].status == "confirmed"

    assert findings[0].comment == "Требование ТЗ нарушено."


async def test_legacy_conflicting_issue_status_cannot_override_decision() -> None:
    """Старый redundant issue.status больше не ломает весь анализ."""
    model, result = await _execute(
        {
            "decisions": {
                "T-R1": {
                    "status": "violated",
                    "confidence": 0.91,
                },
            },
            "issues": [
                {
                    "requirement_id": "T-R1",
                    # Воспроизводим сегодняшнюю production ошибку:
                    # legacy issue.status расходится с authoritative decision.
                    "status": "insufficient_evidence",
                    "severity": "error",
                    "comment": ("Есть конкретное несоответствие."),
                    "evidence": ("На листе показан противоречащий требованию факт."),
                    "recommendation_draft": ("Скорректировать решение."),
                },
            ],
        }
    )

    (
        _summary,
        decisions,
        findings,
        _metrics_result,
    ) = result

    assert model.calls == 1

    assert decisions[0].status == "violated"

    assert (
        len(
            findings,
        )
        == 1
    )

    assert findings[0].status == "confirmed"

    assert findings[0].technical_assignment_source_ids == ("T-R1",)
