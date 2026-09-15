# services/analysis-service/tests/unit/test_technical_assignment_batch_efficiency.py

"""Regression tests производительности exhaustive T-first batching."""

from typing import Any

from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
)
from pdrd_analysis_service.core.settings import (
    PipelineSettings,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
    PageFacts,
)
from pdrd_analysis_service.domain.technical_assignment_validation import (
    TechnicalAssignmentRequirement,
)


class RecordingVisionModel:
    """Fake VLM, возвращающая lossless decisions для каждого batch."""

    def __init__(
        self,
    ) -> None:
        """Создаёт журналы вызовов."""
        self.batch_sizes: list[int] = []
        self.num_predict_values: list[int] = []
        self.stages: list[str] = []

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
        """Возвращает один not_applicable decision на каждый requirement."""
        assert prompt
        assert seed > 0
        assert image_bytes == b"image"

        decisions_schema = schema["properties"]["decisions"]

        requirement_ids = tuple(
            decisions_schema["required"],
        )

        self.batch_sizes.append(
            len(
                requirement_ids,
            )
        )

        self.num_predict_values.append(
            num_predict,
        )

        self.stages.append(
            stage,
        )

        return GenerationResult(
            payload={
                "decisions": {
                    requirement_id: {
                        "status": "not_applicable",
                        "confidence": 0.95,
                    }
                    for requirement_id in requirement_ids
                },
                "issues": [],
            },
            metrics=GenerationMetrics(
                attempt=1,
                done_reason="stop",
                requested_num_predict=num_predict,
                total_duration_ms=10.0,
                load_duration_ms=1.0,
                prompt_eval_count=100,
                eval_count=100,
                content_length=100,
                thinking_length=0,
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def _page_facts() -> PageFacts:
    """Создаёт факты тестового проектного листа."""
    return PageFacts(
        discipline="ЭОМ",
        page_type="scheme",
        summary="Однолинейная схема электроснабжения.",
        objects=("Щит ЩР-1",),
        connections=("ЩР-1 → нагрузка",),
        labels=("QF1",),
        normative_queries=(),
    )


def _requirements(
    count: int,
) -> tuple[
    TechnicalAssignmentRequirement,
    ...,
]:
    """Создаёт заданное число atomic requirements."""
    return tuple(
        TechnicalAssignmentRequirement(
            point_id=f"point-{index}",
            requirement_id=f"T-R{index}",
            requirement_index=index,
            page=1,
            strength="candidate",
            scopes=("electrical",),
            normative_refs=(),
            source_text=(f"Исходный контекст требования {index}."),
            text=(f"Проект должен выполнить требование {index}."),
        )
        for index in range(
            1,
            count + 1,
        )
    )


async def test_default_policy_checks_338_requirements_in_four_batches() -> None:
    """338 requirements проверяются lossless четырьмя initial VLM calls."""
    settings = PipelineSettings()

    assert settings.technical_assignment_batch_size == 96
    assert settings.technical_assignment_num_predict == 7000

    model = RecordingVisionModel()

    use_case = CheckPageAgainstTechnicalAssignment(
        vision_model=model,
        num_predict=(settings.technical_assignment_num_predict),
        batch_size=(settings.technical_assignment_batch_size),
        requirement_text_limit=(settings.technical_assignment_requirement_text_limit),
    )

    requirements = _requirements(
        338,
    )

    (
        summary,
        decisions,
        findings,
        metrics,
    ) = await use_case.execute(
        page_number=19,
        page_type="scheme",
        extracted_text="Однолинейная схема.",
        page_facts=_page_facts(),
        image_bytes=b"image",
        technical_assignment_id=("11111111-1111-4111-8111-111111111111"),
        analysis_document_id=("22222222-2222-4222-8222-222222222222"),
        section_id=("33333333-3333-4333-8333-333333333333"),
        source_file="ТЗ.docx",
        source_sha256="a" * 64,
        requirements=requirements,
    )

    assert model.batch_sizes == [
        96,
        96,
        96,
        50,
    ]

    assert model.num_predict_values == [
        7000,
        7000,
        7000,
        7000,
    ]

    assert model.stages == [
        "technical_assignment_check:19:1",
        "technical_assignment_check:19:2",
        "technical_assignment_check:19:3",
        "technical_assignment_check:19:4",
    ]

    assert (
        sum(
            model.batch_sizes,
        )
        == 338
    )

    assert (
        len(
            decisions,
        )
        == 338
    )

    assert [decision.requirement_id for decision in decisions] == [
        requirement.requirement_id for requirement in requirements
    ]

    assert findings == ()

    assert (
        len(
            metrics,
        )
        == 4
    )

    assert "Проверено требований ТЗ: 338" in summary
