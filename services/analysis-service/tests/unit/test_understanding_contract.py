# services/analysis-service/tests/unit/test_understanding_contract.py

"""Компактная схема убирает обязательную пустоту, сохраняя ёмкость, поля, grounding и графику."""

import json

import pytest
from pdrd_analysis_service.application.json_schemas import build_page_facts_schema
from pdrd_analysis_service.application.understanding_contract import (
    build_understanding_contract,
)
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping


@pytest.mark.parametrize("d,eq", [(0, 0), (12, 0), (0, 40), (12, 40)])
def test_compact_schema_keeps_every_field_limit_and_independent_feature(d, eq):
    """Базовые поля и лимиты прежние; строки спецификации добавляются к контракту D."""
    baseline = build_page_facts_schema(d, eq, 2)
    prompt, schema = build_understanding_contract(
        page_number=20,
        heuristic_page_type="drawing",
        extracted_text="ПРОЕКТНЫЕ ДАННЫЕ",
        max_facts=d,
        max_equipment=eq,
        max_graphical_targets=2,
    )
    assert schema["required"] == baseline["required"] + (
        ["specification_rows"] if d else []
    )
    for name, original in baseline["properties"].items():
        current = schema["properties"][name]
        if name in {"document_facts", "equipment_identities"}:
            assert current["items"]["properties"] == original["items"]["properties"]
            assert current["maxItems"] == original["maxItems"]
            assert set(current["items"]["required"]) < set(
                original["items"]["required"]
            )
        else:
            assert current == original
    assert (
        "ПРОЕКТНЫЕ ДАННЫЕ" in prompt
        and "Не вспоминай ГОСТ, СП или ПУЭ по памяти" in prompt
    )
    assert "Все явно известные значения" in prompt
    if eq:
        assert (
            "manufacturer_evidence_text" in prompt
            and "Не угадывай производителя" in prompt
        )
    assert baseline == build_page_facts_schema(d, eq, 2)


def test_sparse_fact_uses_fewer_tokens_without_changing_domain_meaning():
    """Пустые scope/таблица/идентификаторы не требуют JSON-ключей; известные значения остаются."""
    _, compact = build_understanding_contract(
        page_number=20, heuristic_page_type="drawing", extracted_text=""
    )
    old = build_page_facts_schema()["properties"]["document_facts"]["items"]
    full = dict.fromkeys(old["properties"], "")
    full.update(
        kind="parameter",
        subject_type="equipment",
        property_type="parameter",
        subject_name="Насос P3",
        identifier="P3",
        property_name="Напор",
        value_raw="48",
        unit_raw="м",
        evidence_text="P3, напор 48 м",
        visual_regions=[],
    )
    definition = compact["properties"]["document_facts"]["items"]
    sparse = {
        name: value
        for name, value in full.items()
        if value or name in definition["required"]
    }
    assert (
        set(definition["required"]) <= sparse.keys() <= definition["properties"].keys()
    )
    assert document_fact_from_mapping(full) == document_fact_from_mapping(sparse)
    assert len(json.dumps(sparse)) < len(json.dumps(full))
