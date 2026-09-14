# services/analysis-service/tests/unit/test_finalization_lossless.py

"""Regression tests lossless finalization."""

from typing import Any

from pdrd_analysis_service.application.use_cases.finalization import (
    FinalizeFindings,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    GenerationMetrics,
    GenerationResult,
)


def _metrics() -> GenerationMetrics:
    """Возвращает deterministic metrics."""
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


class PartialFinalizationModel:
    """Имитирует модель, пропустившую часть входных finding IDs."""

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
        """Возвращает только первый finding текущего batch."""
        del prompt
        del num_predict
        del seed
        del stage
        del image_bytes

        finding_ids = schema["properties"]["findings"]["items"]["properties"][
            "finding_id"
        ]["enum"]

        first_id = str(
            finding_ids[0],
        )

        return GenerationResult(
            payload={
                "summary": "partial",
                "findings": [
                    {
                        "finding_id": first_id,
                        "comment": "Финализированное замечание.",
                        "recommendation": "Исправить проектное решение.",
                        "experience_source_ids": [],
                        "normative_source_ids": [],
                    }
                ],
            },
            metrics=_metrics(),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def _finding(
    finding_id: str,
    *,
    index: int,
) -> FindingDraft:
    """Создаёт source-less candidate для finalization."""
    return FindingDraft(
        finding_id=finding_id,
        page=14,
        page_type="scheme",
        category="scheme_logic",
        severity="warning",
        status="needs_review",
        comment=f"Исходное замечание {index}.",
        evidence=f"Конкретный факт {index}.",
        recommendation_draft=f"Проверить решение {index}.",
        confidence=0.7,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query=f"Запрос опыта {index}.",
    )


async def test_finalization_preserves_every_input_finding_when_model_omits_items() -> (
    None
):
    """Пропуск VLM превращается в fallback, а не в удаление finding."""
    input_findings = (
        _finding(
            "p14-f1",
            index=1,
        ),
        _finding(
            "p14-f2",
            index=2,
        ),
        _finding(
            "p14-f3",
            index=3,
        ),
    )

    use_case = FinalizeFindings(
        vision_model=PartialFinalizationModel(),
        num_predict=1800,
        batch_size=3,
        experience_context_limit=600,
        experience_min_score=0.55,
    )

    _, finalized, metrics = await use_case.execute(
        findings=input_findings,
        experience_by_finding={},
    )

    assert tuple(finding.finding_id for finding in finalized) == tuple(
        finding.finding_id for finding in input_findings
    )

    assert len(
        finalized,
    ) == len(
        input_findings,
    )

    assert finalized[0].comment == "Финализированное замечание."
    assert finalized[1].comment == "Исходное замечание 2."
    assert finalized[2].comment == "Исходное замечание 3."

    assert metrics["fallback_count"] == 2


async def test_finalization_preserves_order_for_multiple_batches() -> None:
    """Fallback не меняет порядок findings между batch."""
    input_findings = tuple(
        _finding(
            f"p14-f{index}",
            index=index,
        )
        for index in range(
            1,
            6,
        )
    )

    use_case = FinalizeFindings(
        vision_model=PartialFinalizationModel(),
        num_predict=1800,
        batch_size=2,
        experience_context_limit=600,
        experience_min_score=0.55,
    )

    _, finalized, metrics = await use_case.execute(
        findings=input_findings,
        experience_by_finding={},
    )

    assert tuple(finding.finding_id for finding in finalized) == tuple(
        finding.finding_id for finding in input_findings
    )

    # В каждом batch модель возвращает один finding;
    # остальные обязаны сохраниться через fallback.
    assert metrics["fallback_count"] == 2
