# services/analysis-service/tests/unit/test_understanding_recovery.py

"""Продолжение Understanding после overflow без recovery, выдуманных фактов и потери программных данных."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.application.vision_execution import AnalysisCancelledError
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.domain.equipment_specification import SpecificationCancelled
from pdrd_analysis_service.domain.specification_facts import (
    specification_document_facts,
)

from .test_specification_page_facts import routed_specification


def metrics(reason="length"):
    """Учитывает две физические попытки одного логического запроса адаптера."""
    return GenerationMetrics(
        2, reason, 9600, 100, 0, 300, 9600, 500, 0, slot_wait_ms=80
    )


def model(response=None, error=None):
    """Не разрешает скрытую последовательность восстановительных ответов."""
    return SimpleNamespace(
        generate_json=AsyncMock(return_value=response, side_effect=error)
    )


async def understand(provider, *, d=True, eq=False, text="", source=None, stop=None):
    """Запускает настоящий сценарий с независимыми флагами и сохранённой спецификацией."""
    return await UnderstandPage(
        provider, 3200, image_cropper=SimpleNamespace(crop=AsyncMock())
    ).execute(
        page_number=25,
        heuristic_page_type="drawing",
        extracted_text=text,
        image_bytes=b"original-image",
        use_document_context=d,
        use_equipment_web_search=eq,
        equipment_specification=source,
        should_stop=stop,
    )


@pytest.mark.parametrize(
    "d,eq", [(True, True), (True, False), (False, True), (False, False)]
)
@pytest.mark.parametrize("length_payload", [False, True])
async def test_overflow_has_one_main_request_and_no_recovery(d, eq, length_payload):
    """Ни исключение, ни валидный JSON с length не запускают восстановление или выдуманные цели crop."""
    usage = metrics()
    provider = model(
        GenerationResult(
            {
                "summary": "НЕДОСТОВЕРНЫЙ ОТВЕТ",
                "graphical_targets": [{"object_ref": "FAKE"}],
            },
            usage,
        )
        if length_payload
        else None,
        None
        if length_payload
        else VisionModelOutputBudgetError("overflow", metrics=usage),
    )
    facts, total = await understand(provider, d=d, eq=eq)
    provider.generate_json.assert_awaited_once()
    request = provider.generate_json.await_args.kwargs
    assert request["stage"] == "page_understanding:25"
    assert request["image_bytes"] == b"original-image"
    assert ("document_facts" in request["schema"]["properties"]) is d
    assert ("equipment_identities" in request["schema"]["properties"]) is eq
    assert "graphical_targets" in request["schema"]["properties"]
    assert facts.page_type == "drawing" and facts.summary == facts.discipline == ""
    assert not any(
        (
            facts.objects,
            facts.labels,
            facts.connections,
            facts.normative_queries,
            facts.document_facts,
            facts.equipment_identities,
            facts.graphical_targets,
        )
    )
    assert (
        total.attempt == 2
        and total.eval_count == 9600
        and total.prompt_eval_count == 300
    )
    assert total.slot_wait_ms == 80 and total.requested_num_predict == 9600
    diagnostic = total.as_dict()["understanding"]
    assert diagnostic["main_request_count"] == diagnostic["logical_request_count"] == 1
    assert diagnostic["recovery_count"] == 0 and diagnostic["output_budget_count"] == 1
    assert (
        diagnostic["result"] == "incomplete"
        and diagnostic["limited_reason"] == "output_budget"
    )


@pytest.mark.parametrize(
    "d,eq", [(True, True), (True, False), (False, True), (False, False)]
)
async def test_overflow_keeps_literal_engineering_facts_for_both_d_modes(d, eq):
    """Собственные факты PAGE TEXT сохраняются без выдуманной визуальной области или внешних источников."""
    text = "Назначение системы: водоснабжение (В2). Расчётный расход принят 10 л/с."
    provider = model(error=VisionModelOutputBudgetError("overflow", metrics=metrics()))
    facts, result = await understand(provider, d=d, eq=eq, text=text)
    assert len(facts.document_facts) == 2
    assert all(
        fact.extraction_method == "pdf_text"
        and fact.source_pages == (25,)
        and not fact.visual_regions
        for fact in facts.document_facts
    )
    assert {fact.value_raw for fact in facts.document_facts} == {"водоснабжение", "10"}
    diagnostic = result.as_dict()["understanding"]
    assert diagnostic["programmatic_facts"] == diagnostic["text_facts"] == 2
    assert diagnostic["vlm_facts"] == 0
    assert facts.normative_queries == facts.equipment_identities == ()
    assert provider.generate_json.await_count == 1


@pytest.mark.parametrize("d,eq", [(True, True), (True, False), (False, True)])
async def test_mixed_overflow_keeps_every_table_row_quantity_and_eq_model(d, eq):
    """65 строк не обрезаются лимитом 12 VLM-фактов; D и EQ сохраняют отдельные правила."""
    source = routed_specification("mixed")
    original = json.loads(json.dumps(source))
    provider = model(error=VisionModelOutputBudgetError("overflow", metrics=metrics()))
    facts, result = await understand(
        provider, d=d, eq=eq, text="ТАБЛИЦА", source=source
    )
    assert facts.equipment_specification["raw_rows"] == original["raw_rows"]
    assert facts.equipment_specification["items"] == original["items"]
    assert len(facts.objects) == 65 and len(facts.equipment_identities) == (
        65 if eq else 0
    )
    expected = specification_document_facts(source, 25) if d else ()
    assert facts.document_facts == expected
    if d:
        assert [
            fact.value_raw
            for fact in facts.document_facts
            if fact.column_label == "quantity"
        ] == [str(i) for i in range(1, 66)]
    assert result.as_dict()["understanding"]["programmatic_facts"] == len(expected)
    provider.generate_json.assert_awaited_once()


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({}, "empty_page_facts"),
        ({"summary": "Видимый заголовок"}, "partial_page_facts"),
        (
            {"summary": None, "document_facts": None, "graphical_targets": None},
            "empty_page_facts",
        ),
    ],
)
async def test_empty_or_incomplete_page_facts_do_not_invent_missing_data(
    payload, reason
):
    """Отсутствующие списки и null не превращаются в строки None или фиктивные источники."""
    provider = model(GenerationResult(payload, replace(metrics("stop"), attempt=1)))
    facts, result = await understand(provider)
    assert facts.discipline == "" and facts.page_type == "drawing"
    assert facts.summary == (payload.get("summary") or "")
    assert (
        not facts.document_facts
        and not facts.normative_queries
        and not facts.graphical_targets
    )
    assert result.as_dict()["understanding"]["limited_reason"] == reason
    provider.generate_json.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        VisionModelError("network"),
        RuntimeError("infrastructure"),
        asyncio.CancelledError(),
        AnalysisCancelledError("cancelled"),
    ],
)
async def test_real_errors_and_task_cancellation_are_not_success(error, caplog):
    """Обрабатывается только output budget; сетевой отказ и отмена сохраняют свой тип."""
    provider = model(error=error)
    with caplog.at_level("INFO"), pytest.raises(type(error)):
        await understand(provider)
    provider.generate_json.assert_awaited_once()
    diagnostic = json.loads(
        next(
            record.message.removeprefix("understanding_page ")
            for record in caplog.records
            if record.message.startswith("understanding_page ")
        )
    )
    assert diagnostic["result"] == (
        "cancelled"
        if isinstance(error, (asyncio.CancelledError, AnalysisCancelledError))
        else "failed"
    )
    assert diagnostic["recovery_count"] == 0


async def test_cancel_after_overflow_prevents_successful_continuation():
    """Отмена проверяется после переполнения до формирования неполного результата."""
    provider = model(error=VisionModelOutputBudgetError("overflow", metrics=metrics()))
    with pytest.raises(SpecificationCancelled):
        await understand(provider, stop=AsyncMock(return_value=True))
    provider.generate_json.assert_awaited_once()


async def test_missing_usage_is_not_reported_as_zero_after_overflow():
    """Неизвестная стоимость не выдаётся за отсутствие расхода GPU."""
    _, result = await understand(model(error=VisionModelOutputBudgetError("overflow")))
    assert result.prompt_eval_count is result.eval_count is result.slot_wait_ms is None
    assert result.as_dict()["understanding"]["vlm_call_count"] is None
    assert result.as_dict()["understanding"]["limited_reason"] == "output_budget"
