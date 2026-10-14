# services/analysis-service/tests/unit/test_shared_vlm_finalization_budget.py

"""Типизированное переполнение shared-vlm делит финализацию без потери ID и маскировки ошибки."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings

from .test_finalization_batching import BatchVisionModel, _finding, _metrics


class SharedBudget(BatchVisionModel):
    """Ошибка не содержит legacy-строк Ollama и зависит от размера фактического пакета."""

    async def generate_json(self, **request):
        """Требует изменения размера, не повторения того же batch."""
        ids = request["schema"]["properties"]["findings"]["items"]["properties"][
            "finding_id"
        ]["enum"]
        if len(ids) > 2:
            self.calls += 1
            self.batch_sizes.append(len(ids))
            raise VisionModelOutputBudgetError(
                "shared-vlm output_limit",
                metrics=replace(_metrics(), done_reason="length"),
            )
        return await super().generate_json(**request)


async def test_typed_budget_splits_and_keeps_every_finding_in_order():
    """Неудачные токены остаются в отчёте, все исходные замечания сохраняются."""
    model = SharedBudget()
    initial = tuple(_finding(i) for i in range(1, 5))
    _, final, metrics = await FinalizeFindings(model, 6000, 4, 600, 0.55).execute(
        findings=initial, experience_by_finding={}
    )
    assert [item.finding_id for item in final] == [item.finding_id for item in initial]
    assert model.batch_sizes == [4, 2, 2]
    assert metrics["split_count"] == 1
    assert metrics["batches"][0]["eval_count"] == 50


async def test_irrecoverable_single_finding_budget_remains_explicit_error():
    """Исчерпанная единичная запись не превращается в ложный успешный fallback."""

    class Broken:
        """Имитирует окончательный отказ минимального пакета."""

        async def generate_json(self, **kwargs):
            """Возвращает тип ошибки работающего адаптера."""
            raise VisionModelOutputBudgetError(
                "shared-vlm output_limit", metrics=_metrics()
            )

    with pytest.raises(VisionModelOutputBudgetError):
        await FinalizeFindings(Broken(), 6000, 2, 600, 0.55).execute(
            findings=(_finding(1),), experience_by_finding={}
        )
