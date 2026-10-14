# services/analysis-service/tests/unit/test_vllm_attempt_metrics.py

"""Бюджет, контекст runtime и затраты неудачных HTTP-попыток shared-vlm."""

import pytest
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelOutputBudgetError,
)

from .test_vllm_adapter import (
    FakeAsyncClient,
    _completion,
    _install_fake_client,
    _model,
)


async def generate(model, *, stage="finalization:1", schema=None, budget=4000):
    """Вызывает настоящий адаптер через подменённый HTTP-транспорт."""
    return await model.generate_json(
        prompt="Проверка",
        schema=schema or {"type": "object", "properties": {}},
        num_predict=budget,
        seed=1,
        stage=stage,
    )


async def test_failed_output_and_successful_retry_are_both_counted(monkeypatch):
    """Даже синтаксически корректный JSON с finish_reason=length нельзя принимать."""
    _install_fake_client(
        monkeypatch,
        post_responses=[
            _completion(
                content='{"discard":true}',
                finish_reason="length",
                prompt_tokens=100,
                completion_tokens=4000,
            ),
            _completion(
                content='{"complete":true}', prompt_tokens=130, completion_tokens=20
            ),
        ],
    )
    result = await generate(_model())
    assert result.payload == {"complete": True}
    assert result.metrics.attempt == 2
    assert result.metrics.eval_count == 4020 and result.metrics.prompt_eval_count == 230
    assert result.metrics.requested_num_predict == 12000
    assert [item["json"]["max_tokens"] for item in FakeAsyncClient.requests] == [
        4000,
        8000,
    ]


async def test_terminal_budget_error_preserves_all_failed_usage(monkeypatch):
    """Последняя ошибка хранит и первую попытку; повтор с тем же бюджетом запрещён."""
    _install_fake_client(
        monkeypatch,
        post_responses=[
            _completion(content="{", finish_reason="length", completion_tokens=3200),
            _completion(content="{", finish_reason="length", completion_tokens=6400),
        ],
    )
    with pytest.raises(VisionModelOutputBudgetError) as failure:
        await generate(_model(), budget=3200)
    assert failure.value.metrics.eval_count == 9600
    assert failure.value.metrics.prompt_eval_count == 200
    assert failure.value.metrics.attempt == 2


async def test_runtime_context_prevents_unsafe_output_growth(monkeypatch):
    """Реальный max_model_len ограничивает рост ответа с резервом под повторный промпт."""
    _install_fake_client(
        monkeypatch,
        get_responses=[{}, {"data": [{"id": "shared-vlm", "max_model_len": 8192}]}],
        post_responses=[
            _completion(
                content="{",
                finish_reason="length",
                prompt_tokens=6000,
                completion_tokens=1600,
            ),
            _completion(content="{}", prompt_tokens=6100),
        ],
    )
    model = _model(max_attempts=3, max_retry_num_predict=16000)
    assert await model.is_ready()
    await generate(model, budget=1600)
    assert [
        item["json"]["max_tokens"]
        for item in FakeAsyncClient.requests
        if item["method"] == "POST"
    ] == [1600, 1680]


async def test_large_combined_page_splits_before_repeating_same_schema(monkeypatch):
    """Общий D/EQ-ответ после length сразу передаётся существующему recovery, без роста до 16000."""
    _install_fake_client(
        monkeypatch,
        post_responses=[
            _completion(content="{", finish_reason="length", completion_tokens=4000),
        ],
    )
    with pytest.raises(VisionModelOutputBudgetError):
        await generate(
            _model(max_attempts=3, max_retry_num_predict=16000),
            stage="page_understanding:20",
            schema={"properties": {"document_facts": {}, "equipment_identities": {}}},
        )
    assert len(FakeAsyncClient.requests) == 1


async def test_invalid_transport_payload_is_unknown_usage_not_zero(monkeypatch):
    """Неверный completion учитывается как HTTP-попытка, но не как известный нулевой расход GPU."""
    _install_fake_client(monkeypatch, post_responses=[{}, _completion(content="{}")])
    result = await generate(_model())
    assert result.metrics.attempt == 2
    assert (
        result.metrics.prompt_eval_count is None and result.metrics.eval_count is None
    )
    assert result.metrics.total_duration_ms >= 0
