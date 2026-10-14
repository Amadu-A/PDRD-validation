# services/analysis-service/tests/unit/test_equipment_comparison.py

"""Регрессия идентичности оборудования и безопасного EQ-сопоставления."""

from pdrd_analysis_service.domain.equipment import (
    EquipmentFact,
    EquipmentOccurrence,
    InventoryItem,
    build_inventory,
    compare_equipment,
)


def test_inventory_deduplicates_model_without_losing_occurrences() -> None:
    """Одна модель на двух листах даёт один поиск и два доказательства."""
    pages = [
        {
            "page_number": page,
            "facts": {
                "equipment_identities": [
                    {
                        "manufacturer": "MEAN WELL",
                        "model": "DRC-100B",
                        "variant": "",
                        "status": "resolved",
                        "confidence": 0.96,
                        "object_ref": f"БП-{page}",
                        "evidence_text": "DRC-100B",
                        "visual_regions": [{"x_min": 1, "y_min": 2}],
                        "parameters": [{"property_name": "output voltage"}],
                    }
                ]
            },
        }
        for page in (1, 2)
    ]
    items = build_inventory(pages)
    assert len(items) == 1
    assert [part.page for part in items[0].occurrences] == [1, 2]
    assert [part.object_ref for part in items[0].occurrences] == ["БП-1", "БП-2"]


def test_conflicting_variants_remain_separate_and_need_review() -> None:
    """Несовместимые исполнения одного объекта не подтверждаются автоматически."""
    pages = [
        {
            "page_number": 1,
            "facts": {
                "equipment_identities": [
                    {
                        "manufacturer": "CHINT",
                        "model": "NXB-63",
                        "variant": variant,
                        "status": "resolved",
                        "confidence": 0.95,
                        "object_ref": "QF1",
                        "parameters": [],
                    }
                    for variant in ("C16", "D16")
                ]
            },
        }
    ]
    items = build_inventory(pages)
    assert len(items) == 2
    assert {item.variant for item in items} == {"C16", "D16"}
    assert all(item.status == "needs_review" for item in items)


def _item(value: str, unit: str, current_type: str = "DC") -> InventoryItem:
    """Создаёт одно проектное значение с визуальным доказательством."""
    return InventoryItem(
        manufacturer="MEAN WELL",
        model="DRC-100B",
        variant="",
        status="resolved",
        confidence=0.98,
        occurrences=(
            EquipmentOccurrence(
                page=3,
                object_ref="БП1",
                evidence_text="Выход 24 В DC",
                visual_regions=({"x_min": 1, "y_min": 2},),
                parameters=(
                    {
                        "property_name": "output voltage",
                        "value_raw": value,
                        "unit_raw": unit,
                        "role": "output",
                        "current_type": current_type,
                        "mode": "normal",
                        "evidence_text": "Выход 24 В DC",
                    },
                ),
            ),
        ),
    )


def _fact(
    value: str,
    unit: str,
    *,
    role: str = "output",
    current_type: str = "DC",
    variant: str = "",
) -> EquipmentFact:
    """Создаёт характеристику с точным EQ snapshot."""
    return EquipmentFact(
        property_name="output voltage",
        value_raw=value,
        unit_raw=unit,
        role=role,
        current_type=current_type,
        mode="normal",
        variant=variant,
        source_id="EQ-immutable",
        source_page=2,
        snippet=f"DRC-100B output voltage {value} {unit}",
    )


def test_range_and_compatible_units_create_stable_candidate() -> None:
    """Выход за диапазон после пересчёта мВ создаёт EQ-кандидата."""
    result = compare_equipment(_item("26000", "mV"), [_fact("21...29", "V")])
    assert len(result) == 1
    assert result[0].status == "matched"
    mismatch = compare_equipment(_item("32000", "mV"), [_fact("21...29", "V")])
    assert mismatch[0].status == "mismatch_candidate"
    assert mismatch[0].source_id == "EQ-immutable"
    assert mismatch[0].page == 3
    assert mismatch[0].visual_regions
    assert mismatch == compare_equipment(_item("32000", "mV"), [_fact("21...29", "V")])


def test_scope_and_ambiguity_prevent_false_violation() -> None:
    """AC/DC, роль, исполнение и скаляр блокируют категорический вывод."""
    assert not compare_equipment(
        _item("32", "V"), [_fact("21...29", "V", role="input")]
    )
    assert not compare_equipment(_item("32", "V", "AC"), [_fact("21...29", "V")])
    assert not compare_equipment(_item("32", "V"), [_fact("21...29", "V", variant="A")])
    assert (
        compare_equipment(_item("32", "V"), [_fact("24", "V")])[0].status
        == "needs_review"
    )


def test_numeric_text_and_wrong_dimension_cannot_create_violation() -> None:
    """Оговорки и неверная физическая размерность не сравниваются как числа."""
    assert not compare_equipment(_item("32", "V"), [_fact("not 21...29", "V")])
    assert not compare_equipment(_item("32", "W"), [_fact("21...29", "W")])
    assert (
        compare_equipment(_item("26000", "мВ"), [_fact("21...29", "В")])[0].status
        == "matched"
    )


def test_discrete_parameters_compare_sets_and_explicit_boolean() -> None:
    """Допустимое перечисление и требование заземления сравниваются отдельно."""
    from dataclasses import replace

    for property_name, project, maker, expected in (
        ("trip_curve", "C", "B,C,D", "matched"),
        ("trip_curve", "Z", "B,C,D", "mismatch_candidate"),
        ("requires_earthing", "да", "required", "matched"),
        ("requires_earthing", "нет", "required", "mismatch_candidate"),
        ("protection_degree", "IP65", "IP65", "matched"),
    ):
        initial = _item(project, "", current_type="")
        occurrence = replace(
            initial.occurrences[0],
            parameters=(
                {
                    "property_name": property_name,
                    "value_raw": project,
                    "unit_raw": "",
                    "role": "",
                    "current_type": "",
                    "mode": "normal",
                },
            ),
        )
        item = replace(initial, occurrences=(occurrence,))
        fact = replace(
            _fact(maker, "", role="", current_type=""), property_name=property_name
        )
        result = compare_equipment(item, [fact])
        assert result[0].status == expected


def test_text_difference_needs_review_without_semantic_guess() -> None:
    """Текстовое расхождение сохраняется для общего Finalization."""
    from dataclasses import replace

    item = _item("normal", "", current_type="")
    part = replace(
        item.occurrences[0],
        parameters=(
            {
                "property_name": "contact_configuration",
                "value_raw": "2 NO",
                "unit_raw": "",
                "mode": "normal",
            },
        ),
    )
    fact = replace(
        _fact("2 changeover contacts", "", role="", current_type=""),
        property_name="contact_configuration",
        value_type="text",
    )
    assert (
        compare_equipment(replace(item, occurrences=(part,)), [fact])[0].status
        == "needs_review"
    )


def test_candidate_id_separates_modes_and_document_pages() -> None:
    """Разные provenance и режимы не получают один идентификатор кандидата."""
    from dataclasses import replace

    first = replace(_fact("21...29", "V"), condition="at full load")
    second = replace(first, source_page=3, condition="at no load")
    results = compare_equipment(_item("32", "V"), [first, second])
    assert len({row.candidate_id for row in results}) == 2
    assert {row.status for row in results} == {"needs_review"}


def test_reversed_range_is_not_treated_as_an_approved_interval() -> None:
    """Ошибочная последовательность границ не исправляется предположительно."""
    assert not compare_equipment(_item("32", "V"), [_fact("29...21", "V")])
