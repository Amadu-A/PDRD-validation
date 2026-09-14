# services/analysis-service/tests/unit/test_finalization_batching.py

"""Unit и synthetic-load tests batching финализации."""

from typing import Any

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
)
from pdrd_analysis_service.application.use_cases import (
    FinalizeFindings,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    GenerationMetrics,
    GenerationResult,
)


def _metrics() -> GenerationMetrics:
    """Возвращает deterministic metrics одного VLM call."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=1800,
        total_duration_ms=10.0,
        load_duration_ms=1.0,
        prompt_eval_count=100,
        eval_count=50,
        content_length=300,
        thinking_length=0,
    )


def _finding(
    index: int,
) -> FindingDraft:
    """Создаёт source-less finding для batch tests."""
    return FindingDraft(
        finding_id=f"p1-f{index}",
        page=1,
        page_type="scheme",
        category="scheme_logic",
        severity="warning",
        status="needs_review",
        comment=f"Исходное замечание {index}.",
        evidence=f"Конкретный факт {index}.",
        recommendation_draft=f"Проверить решение {index}.",
        confidence=0.8,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query=f"Опыт {index}.",
    )


class BatchVisionModel:
    """Fake VLM с учётом размеров batch."""

    def __init__(
        self,
        *,
        fail_above: int | None = None,
        failure_message: str = ("Ollama исчерпал output budget на этапе finalization."),
    ) -> None:
        """Сохраняет параметры controlled failure."""
        self.fail_above = fail_above

        self.failure_message = failure_message

        self.calls = 0

        self.batch_sizes: list[int] = []

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
        """Возвращает все IDs или controlled VisionModelError."""
        assert prompt
        assert schema
        assert num_predict > 0
        assert seed > 0
        assert stage.startswith(
            "finalization:",
        )
        assert image_bytes is None

        finding_ids = tuple(
            str(
                finding_id,
            )
            for finding_id in (
                schema["properties"]["findings"]["items"]["properties"]["finding_id"][
                    "enum"
                ]
            )
        )

        self.calls += 1

        self.batch_sizes.append(
            len(
                finding_ids,
            )
        )

        if (
            self.fail_above is not None
            and len(
                finding_ids,
            )
            > self.fail_above
        ):
            raise VisionModelError(
                self.failure_message,
            )

        return GenerationResult(
            payload={
                "summary": "done",
                "findings": [
                    {
                        "finding_id": finding_id,
                        "comment": (f"Финализировано {finding_id}."),
                        "recommendation": (f"Исправить {finding_id}."),
                        "experience_source_ids": [],
                        "normative_source_ids": [],
                    }
                    for finding_id in finding_ids
                ],
            },
            metrics=_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def _use_case(
    model: BatchVisionModel,
    *,
    batch_size: int,
) -> FinalizeFindings:
    """Создаёт use case с заданным batch size."""
    return FinalizeFindings(
        vision_model=model,
        num_predict=1800,
        batch_size=batch_size,
        experience_context_limit=600,
        experience_min_score=0.55,
    )


async def test_fifty_findings_use_configured_batch_size() -> None:
    """50 findings не превращаются в 50 VLM calls."""
    model = BatchVisionModel()

    findings = tuple(
        _finding(
            index,
        )
        for index in range(
            1,
            51,
        )
    )

    _, finalized, metrics = await _use_case(
        model,
        batch_size=4,
    ).execute(
        findings=findings,
        experience_by_finding={},
    )

    assert (
        len(
            finalized,
        )
        == 50
    )

    assert tuple(finding.finding_id for finding in finalized) == tuple(
        finding.finding_id for finding in findings
    )

    assert model.calls == 13

    assert model.batch_sizes == ([4] * 12 + [2])

    assert metrics["effective_batch_size"] == 4

    assert metrics["initial_batch_count"] == 13

    assert metrics["vlm_call_count"] == 13

    assert metrics["successful_vlm_call_count"] == 13

    assert metrics["failed_vlm_call_count"] == 0

    assert metrics["fallback_count"] == 0

    assert metrics["split_count"] == 0


async def test_size_related_failure_splits_batch_and_recovers() -> None:
    """Output/context failure делит batch вместо потери findings."""
    model = BatchVisionModel(
        fail_above=2,
    )

    findings = tuple(
        _finding(
            index,
        )
        for index in range(
            1,
            5,
        )
    )

    _, finalized, metrics = await _use_case(
        model,
        batch_size=4,
    ).execute(
        findings=findings,
        experience_by_finding={},
    )

    assert tuple(finding.finding_id for finding in finalized) == tuple(
        finding.finding_id for finding in findings
    )

    assert model.calls == 3

    assert model.batch_sizes == [
        4,
        2,
        2,
    ]

    assert metrics["vlm_call_count"] == 3

    assert metrics["successful_vlm_call_count"] == 2

    assert metrics["failed_vlm_call_count"] == 1

    assert metrics["split_count"] == 1

    assert metrics["fallback_count"] == 0


async def test_transport_failure_does_not_fan_out_requests() -> None:
    """Transport/GPU error не должен порождать каскад split calls."""
    model = BatchVisionModel(
        fail_above=0,
        failure_message=("Не удалось обратиться к Ollama: temporary network failure."),
    )

    findings = tuple(
        _finding(
            index,
        )
        for index in range(
            1,
            5,
        )
    )

    _, finalized, metrics = await _use_case(
        model,
        batch_size=4,
    ).execute(
        findings=findings,
        experience_by_finding={},
    )

    assert model.calls == 1

    assert model.batch_sizes == [
        4,
    ]

    assert tuple(finding.finding_id for finding in finalized) == tuple(
        finding.finding_id for finding in findings
    )

    assert tuple(finding.comment for finding in finalized) == tuple(
        finding.comment for finding in findings
    )

    assert metrics["vlm_call_count"] == 1

    assert metrics["successful_vlm_call_count"] == 0

    assert metrics["failed_vlm_call_count"] == 1

    assert metrics["split_count"] == 0

    assert metrics["fallback_count"] == 4

    assert metrics["done_reason"] == ("completed_with_fallback")
