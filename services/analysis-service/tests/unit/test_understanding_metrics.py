# services/analysis-service/tests/unit/test_understanding_metrics.py

"""Учёт GPU отличает переполнение от recovery, сохраняет расходы и исключает cache hit."""

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
from pdrd_analysis_service.application.understanding_recovery import (
    limited_understanding_payload,
)
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.analysis import GenerationResult

from .test_equipment_specification import metrics
from .test_vllm_cache import RecordingVisionModel, _generate, _model


async def test_failed_attempts_graphical_refinement_and_cache_are_counted():
    """Две попытки основного запроса, графическое уточнение и кеш не означают recovery."""
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
        await recorder.generate_json(stage="page_understanding:20")
    await recorder.generate_json(stage="graphical_refinement:20:1")
    await recorder.generate_json(stage="graphical_refinement:20:1")
    result = recorder.result({"result": "completed"})
    assert (
        result.attempt == 3
        and result.eval_count == 9650
        and result.prompt_eval_count == 300
    )
    diagnostic = result.as_dict()["understanding"]
    assert diagnostic["recovery_count"] == 0 and diagnostic["output_budget_count"] == 1
    assert (
        diagnostic["main_request_count"] == 1
        and diagnostic["logical_request_count"] == 3
    )
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


async def test_understanding_cache_hit_has_zero_physical_calls_and_preserves_facts(
    tmp_path,
):
    """Кеш полного основного ответа даёт те же PAGE FACTS, нулевые токены и отдельный cache hit."""
    delegate = RecordingVisionModel(
        payload={**limited_understanding_payload("drawing"), "summary": "Схема S7"}
    )
    use_case = UnderstandPage(_model(delegate=delegate, root_path=tmp_path), 4000)
    arguments = {
        "page_number": 7,
        "heuristic_page_type": "drawing",
        "extracted_text": "Схема S7",
        "image_bytes": b"same-image",
        "use_document_context": False,
    }
    first, initial = await use_case.execute(**arguments)
    second, cached = await use_case.execute(**arguments)
    assert first == second and delegate.calls == initial.attempt == 1
    assert cached.attempt == cached.eval_count == cached.prompt_eval_count == 0
    diagnostic = cached.as_dict()["understanding"]
    assert diagnostic["main_request_count"] == diagnostic["cache_hits"] == 1
    assert diagnostic["recovery_count"] == diagnostic["output_budget_count"] == 0
    assert diagnostic["limited_reason"] is None and diagnostic["result"] == "completed"
