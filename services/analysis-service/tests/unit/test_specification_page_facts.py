# services/analysis-service/tests/unit/test_specification_page_facts.py

"""Программные факты, безопасный выбор маршрута и независимость D от EQ."""

import json
from dataclasses import asdict, replace

import pytest
from pdrd_analysis_service.application.ports.vision_model import VisionModelError
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.analysis import GenerationResult
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping
from pdrd_analysis_service.domain.specification_facts import (
    specification_document_facts,
)

from .test_equipment_specification import metrics, model, specification


def routed_specification(route="pdf_table"):
    """Содержит доказательство программного маршрута, получаемое от PDF reader."""
    source = specification(65)
    source["page_content"] = {
        "version": 1,
        "route": route,
        "requires_vision": route != "pdf_table",
        "outside_text": "Вне таблицы: вентиляционная схема",
        "table_regions": [{"x_min": 10, "y_min": 0, "x_max": 900, "y_max": 70}],
        "reason": "non_table_content"
        if route == "mixed"
        else "text_table_and_title_block",
    }
    return source


async def understand(provider, source, *, d=True, eq=True):
    """Запускает тот же сценарий с исходным контрактом и выбранными флагами."""
    return await UnderstandPage(provider, 4000).execute(
        page_number=25,
        heuristic_page_type="specification",
        extracted_text="ВСЯ ТАБЛИЦА",
        image_bytes=b"image",
        use_document_context=d,
        use_equipment_web_search=eq,
        equipment_specification=source,
    )


@pytest.mark.parametrize("d,eq", [(True, False), (False, True), (True, True)])
async def test_programmatic_table_has_no_primary_vlm_and_keeps_all_values(d, eq):
    """65 позиций и количества сохраняются без GPU; EQ выключен независимо от D."""
    provider, source = model(), routed_specification()
    facts, result = await understand(provider, source, d=d, eq=eq)
    provider.generate_json.assert_not_awaited()
    assert len(facts.objects) == 65
    assert len(facts.equipment_identities) == (65 if eq else 0)
    assert len(facts.document_facts) > 12 if d else not facts.document_facts
    assert result.attempt == result.eval_count == result.prompt_eval_count == 0
    assert result.as_dict()["understanding"]["route"] == "pdf_table"
    if d:
        quantities = [
            fact for fact in facts.document_facts if fact.column_label == "quantity"
        ]
        assert [fact.value_raw for fact in quantities] == [str(i) for i in range(1, 66)]
        assert len({fact.identifier for fact in quantities}) == 65
        for fact in facts.document_facts:
            assert (
                fact.source_row_ids
                and fact.source_pages == (25,)
                and fact.source_regions
            )
            assert document_fact_from_mapping(asdict(fact)) == fact


@pytest.mark.parametrize("metadata", [None, {"version": 2}, {"requires_vision": True}])
async def test_incomplete_route_proof_never_skips_vision(metadata):
    """Без версии и явного доказательства чистой таблицы сохраняется визуальный маршрут."""
    provider, source = model(), routed_specification()
    if metadata is None:
        source.pop("page_content")
    else:
        source["page_content"].update(metadata)
    _, result = await understand(provider, source)
    provider.generate_json.assert_awaited_once()
    assert result.as_dict()["understanding"]["route"] == "mixed"


async def test_mixed_page_only_generates_non_table_facts_and_discards_same_row_copy():
    """Повтор таблицы исключается по области; факт схемы вне таблицы сохраняется."""
    provider, source = model(), routed_specification("mixed")
    table_fact = specification_document_facts(source, 25)[0]
    duplicate = replace(table_fact, source_item_id="", extraction_method="")
    outside = replace(
        duplicate,
        identifier="V1",
        scope_segment="V1",
        value_raw="Схема",
        evidence_text="Вентилятор V1",
        visual_regions=(
            {
                "x_min": 100,
                "y_min": 800,
                "x_max": 300,
                "y_max": 900,
                "confidence": 0.9,
            },
        ),
    )
    provider.generate_json.return_value = GenerationResult(
        {
            "discipline": "ОВ",
            "page_type": "scheme",
            "summary": "Вентиляционная схема",
            "objects": ["V1"],
            "connections": [],
            "labels": [],
            "normative_queries": [],
            "document_facts": [asdict(duplicate), asdict(outside)],
        },
        metrics(),
    )
    provider.generate_json.side_effect = None
    facts, result = await understand(provider, source)
    request = provider.generate_json.await_args.kwargs
    assert "equipment_identities" not in request["schema"]["properties"]
    assert "ВСЯ ТАБЛИЦА" not in request["prompt"]
    assert (
        "Вне таблицы" in request["prompt"]
        and "только сведения вне таблицы" in request["prompt"]
    )
    assert (
        len(facts.document_facts) == len(specification_document_facts(source, 25)) + 1
    )
    assert facts.document_facts[-1].identifier == "V1"
    assert len(facts.equipment_identities) == 65 and "V1" in facts.objects
    assert result.as_dict()["understanding"]["vlm_facts"] == 1


async def test_failure_diagnostics_do_not_contain_document_or_provider_response(caplog):
    """Структурированный журнал ошибки содержит код и счётчики, сохраняя саму ошибку."""
    provider, source = model(), routed_specification("mixed")
    source["page_content"]["reason"] = "СЕКРЕТ ДОКУМЕНТА"
    provider.generate_json.side_effect = VisionModelError("СЕКРЕТ ОТВЕТА")
    with (
        caplog.at_level("INFO"),
        pytest.raises(VisionModelError, match="СЕКРЕТ ОТВЕТА"),
    ):
        await understand(provider, source)
    message = next(
        record.message
        for record in caplog.records
        if record.message.startswith("understanding_page ")
    )
    diagnostic = json.loads(message.removeprefix("understanding_page "))
    assert (
        diagnostic["result"] == "failed" and diagnostic["error_code"] == "stage_error"
    )
    assert "СЕКРЕТ" not in message


def test_confirmed_parameters_deduplicate_same_row_and_keep_distinct_modes():
    """Одно доказательство не удваивается; явно разные режимы остаются независимыми фактами."""
    source = specification(1)
    parameter = {
        "property_name": "voltage",
        "value_raw": "220",
        "unit_raw": "В",
        "evidence_status": "confirmed",
        "evidence_text": "220 В, питание",
        "role": "supply",
        "mode": "normal",
    }
    source["items"][0]["parameters"] = [
        parameter,
        dict(parameter),
        {**parameter, "mode": "reserve"},
    ]
    facts = specification_document_facts(source, 25)
    voltages = [fact for fact in facts if fact.property_name == "voltage"]
    assert len(voltages) == 2
    assert {fact.scope_operating_mode for fact in voltages} == {"normal", "reserve"}
