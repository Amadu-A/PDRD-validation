# services/analysis-service/tests/unit/test_normative_bounded_batches.py

"""Большой лист проходит короткими ответами без потери кандидатов и слепых повторов."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.use_cases.normative import CheckPageAgainstNorms
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult


def metrics(*, reason="stop", budget=14000):
    """Метрики модели для детерминированной проверки планировщика."""
    return GenerationMetrics(1, reason, budget, 10, 0, 100, 100, 300, 0)


def candidate(index):
    """Различимый факт сохраняет собственные источники и область."""
    return {
        "category": "marking",
        "severity": "error",
        "status": "needs_review",
        "comment": f"Нет маркировки узла {index}",
        "evidence": f"На узле {index} отсутствует обозначение",
        "recommendation_draft": "",
        "confidence": 0.8,
        "object_ref": f"Узел {index}",
        "normative_source_ids": ["N1"],
        "technical_assignment_source_ids": ["T1"],
        "user_package_source_ids": ["U1"],
        "document_context_source_ids": ["D1"],
        "visual_regions": [
            {"x_min": index, "y_min": 100, "x_max": index + 1, "y_max": 120}
        ],
    }


async def discover(model, **options):
    """Запускает production discovery с большим общим лимитом замечаний."""
    return await CheckPageAgainstNorms(model, 14000, 500, 700)._discover_candidates(
        page_number=22,
        prompt="Контекст большого листа",
        image_bytes=b"image",
        normative_source_ids=("N1",),
        technical_assignment_source_ids=("T1",),
        user_package_source_ids=("U1",),
        document_context_source_ids=("D1",),
        **options,
    )


async def test_thirty_seven_candidates_keep_all_sources_across_four_bounded_calls():
    """10/10/10/7 — ёмкость ответа, а не общий предел проверки листа."""
    remaining = list(range(37))

    async def generate(**request):
        """Возвращает столько фактов, сколько допускает текущая схема."""
        capacity = request["schema"]["properties"]["violations"]["maxItems"]
        indexes, remaining[:] = remaining[:capacity], remaining[capacity:]
        return GenerationResult(
            {
                "summary": "Контекст",
                "violations": [candidate(index) for index in indexes],
            },
            metrics(budget=request["num_predict"]),
        )

    provider = SimpleNamespace(generate_json=AsyncMock(side_effect=generate))
    _, selection, _ = await discover(provider)
    assert provider.generate_json.await_count == 4
    assert (
        selection.consolidated_count
        == selection.generated_count
        == selection.represented_count
        == 37
    )
    assert [item["object_ref"] for item in selection.candidates] == [
        f"Узел {index}" for index in range(37)
    ]
    for request in provider.generate_json.await_args_list:
        assert request.kwargs["schema"]["properties"]["violations"]["maxItems"] <= 10
    for item in selection.candidates:
        assert item["normative_source_ids"] == ["N1"] and item[
            "technical_assignment_source_ids"
        ] == ["T1"]
        assert item["user_package_source_ids"] == ["U1"] and item[
            "document_context_source_ids"
        ] == ["D1"]
        assert item["visual_regions"]


async def test_exhausted_bulk_keeps_probe_and_reduces_next_schema():
    """Ошибка большого ответа не теряет первые десять кандидатов и меняет задание."""

    async def generate(**request):
        """Имитирует output limit только первой bulk-порции."""
        stage = request["stage"]
        if stage.endswith("probe1"):
            return GenerationResult(
                {"violations": [candidate(i) for i in range(10)]}, metrics()
            )
        if stage.endswith(":bulk"):
            raise VisionModelOutputBudgetError(
                "output budget", metrics=metrics(reason="length")
            )
        assert request["schema"]["properties"]["violations"]["maxItems"] == 5
        assert "Нет маркировки узла 0" in request["prompt"]
        return GenerationResult(
            {"violations": [candidate(10), candidate(11)]}, metrics()
        )

    provider = SimpleNamespace(generate_json=AsyncMock(side_effect=generate))
    _, selection, combined = await discover(provider)
    assert (
        selection.consolidated_count == 12 and provider.generate_json.await_count == 3
    )
    assert combined.total_duration_ms == 30
    assert combined.done_reason == "stop"


async def test_smallest_failing_batch_is_not_repeated():
    """Схемы 10/5/2/1 ограничивают восстановление; единичная ошибка остаётся явной."""
    provider = SimpleNamespace(
        generate_json=AsyncMock(
            side_effect=VisionModelOutputBudgetError("output budget")
        )
    )
    with pytest.raises(VisionModelOutputBudgetError):
        await discover(provider)
    capacities = [
        request.kwargs["schema"]["properties"]["violations"]["maxItems"]
        for request in provider.generate_json.await_args_list
    ]
    assert capacities == [10, 5, 2, 1]
    assert (
        len(
            {
                request.kwargs["stage"]
                for request in provider.generate_json.await_args_list
            }
        )
        == 4
    )


async def test_network_error_does_not_trigger_budget_split():
    """Изменение порции разрешено только для типизированного исчерпания ответа."""
    provider = SimpleNamespace(
        generate_json=AsyncMock(side_effect=VisionModelError("network unavailable"))
    )
    with pytest.raises(VisionModelError):
        await discover(provider)
    assert provider.generate_json.await_count == 1


async def test_cancel_after_probe_does_not_start_continuation():
    """Scheduler отменяет следующий вызов через существующий независимый callback."""
    provider = SimpleNamespace(
        generate_json=AsyncMock(
            return_value=GenerationResult(
                {"violations": [candidate(i) for i in range(10)]}, metrics()
            )
        )
    )
    cancel = AsyncMock(side_effect=RuntimeError("cancelled"))
    with pytest.raises(RuntimeError, match="cancelled"):
        await discover(provider, check_cancellation=cancel)
    assert provider.generate_json.await_count == 1


async def test_complete_json_at_output_limit_preserves_findings_and_continues_with_smaller_schema():
    """Завершённая JSON-форма на границе токенов не выдаётся за окончание проверки."""

    async def generate(**request):
        """Первый допустимый JSON содержит меньше полной порции, но finish_reason=length."""
        if request["stage"].endswith("probe1"):
            return GenerationResult(
                {"violations": [candidate(0), candidate(1)]}, metrics(reason="length")
            )
        assert request["schema"]["properties"]["violations"]["maxItems"] == 5
        return GenerationResult({"violations": [candidate(2)]}, metrics())

    provider = SimpleNamespace(generate_json=AsyncMock(side_effect=generate))
    _, selection, _ = await discover(provider)
    assert selection.consolidated_count == 3 and provider.generate_json.await_count == 2


async def test_empty_complete_json_at_token_limit_does_not_silently_finish_check():
    """Пустой формально допустимый ответ length требует уменьшенного продолжения."""

    async def generate(**request):
        """Первый ответ пустой из-за лимита, второй содержит полноценное замечание."""
        if request["stage"].endswith("probe1"):
            return GenerationResult({"violations": []}, metrics(reason="length"))
        assert request["schema"]["properties"]["violations"]["maxItems"] == 5
        assert request["num_predict"] == 14000
        return GenerationResult({"violations": [candidate(1)]}, metrics())

    provider = SimpleNamespace(generate_json=AsyncMock(side_effect=generate))
    _, selection, _ = await discover(provider)
    assert selection.consolidated_count == 1 and provider.generate_json.await_count == 2


async def test_complete_length_at_single_candidate_capacity_remains_explicit_error():
    """После 10/5/2/1 непрерывный length нельзя выдать за завершённую проверку."""
    provider = SimpleNamespace(
        generate_json=AsyncMock(
            return_value=GenerationResult({"violations": []}, metrics(reason="length"))
        )
    )
    with pytest.raises(VisionModelOutputBudgetError, match="единичной порции"):
        await discover(provider)
    assert [
        call.kwargs["schema"]["properties"]["violations"]["maxItems"]
        for call in provider.generate_json.await_args_list
    ] == [10, 5, 2, 1]
