# services/analysis-service/tests/unit/test_understanding_metrics.py

"""Учёт GPU после ошибки, recovery и кеша не теряет расходы и не выдумывает нулевой usage."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.understanding_metrics import (
    UnderstandingMeasurements,
)
from pdrd_analysis_service.domain.analysis import GenerationResult

from .test_equipment_specification import metrics
from .test_vllm_cache import RecordingVisionModel, _generate, _model


async def test_all_failed_attempts_recovery_and_cache_are_counted():
    """Адаптерные две попытки, новый recovery и cache hit составляют три вызова GPU."""
    failed = replace(
        metrics(),
        attempt=2,
        done_reason="length",
        eval_count=9600,
        prompt_eval_count=200,
    )
    recovered = replace(metrics(), eval_count=50, prompt_eval_count=100)
    cached = replace(
        metrics(), done_reason="cache_hit", attempt=1, eval_count=0, prompt_eval_count=0
    )
    recorder = UnderstandingMeasurements(
        SimpleNamespace(
            generate_json=AsyncMock(
                side_effect=[
                    VisionModelOutputBudgetError("budget", metrics=failed),
                    GenerationResult({}, recovered),
                    GenerationResult({}, cached),
                ]
            )
        )
    )
    with pytest.raises(VisionModelOutputBudgetError):
        await recorder.generate_json()
    await recorder.generate_json()
    await recorder.generate_json()
    result = recorder.result({"result": "completed"})
    assert (
        result.attempt == 3
        and result.eval_count == 9650
        and result.prompt_eval_count == 300
    )
    assert result.as_dict()["understanding"]["recovery_count"] == 1
    assert result.as_dict()["understanding"]["cache_hits"] == 1
    assert (
        result.requested_num_predict
        == failed.requested_num_predict + recovered.requested_num_predict
    )


async def test_unmeasured_failure_never_becomes_known_zero_usage():
    """Даже после успешного продолжения неизвестная стоимость предыдущей ошибки остаётся null."""
    recorder = UnderstandingMeasurements(
        SimpleNamespace(
            generate_json=AsyncMock(
                side_effect=[
                    VisionModelError("provider unavailable"),
                    GenerationResult({}, metrics()),
                ]
            )
        )
    )
    with pytest.raises(VisionModelError):
        await recorder.generate_json()
    await recorder.generate_json()
    result = recorder.result({"result": "completed"})
    assert result.attempt == 2
    assert result.eval_count is None and result.prompt_eval_count is None


async def test_real_persistent_cache_hit_keeps_one_provider_call(tmp_path):
    """Настоящий файловый кеш возвращает канонический ответ и не увеличивает счётчик GPU."""
    delegate = RecordingVisionModel(payload={"value": "canonical"})
    recorder = UnderstandingMeasurements(_model(delegate=delegate, root_path=tmp_path))
    first = await _generate(recorder)
    second = await _generate(recorder)
    result = recorder.result({"result": "completed"})
    assert first.payload == second.payload == {"value": "canonical"}
    assert delegate.calls == result.attempt == 1
    assert result.prompt_eval_count == 100 and result.eval_count == 20
    assert result.requested_num_predict == 4000
    assert result.as_dict()["understanding"]["cache_hits"] == 1
