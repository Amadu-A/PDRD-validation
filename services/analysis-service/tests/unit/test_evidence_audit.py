# services/analysis-service/tests/unit/test_evidence_audit.py

"""Доказательства, сопоставимость и независимые претензии без готовых ответов модели."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
    normalized_value,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    assertion_from_facts,
    read_assertion,
    same_assertion,
)
from pdrd_analysis_service.domain.evidence_audit import audit_assertion, bind_evidence


def observation(
    value="10",
    *,
    page=1,
    identifier="S1",
    prop="Расход воды",
    unit="л/с",
    quote=None,
    **extra,
):
    """Создаёт исходную запись; ожидаемый инженерный вывод в факт не помещается."""
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "subject_type": "system",
            "identifier": identifier,
            "property_type": "parameter",
            "property_name": prop,
            "value_raw": value,
            "unit_raw": unit,
            "evidence_text": quote
            if quote is not None
            else f"{identifier} {prop}: {value} {unit}",
            **extra,
        }
    )
    assert fact is not None
    return LocatedDocumentFact(page, 1, fact)


def assess(*items, texts=None, claim_type="contradiction"):
    """Проверяет утверждение только относительно переданных физических страниц."""
    assertion = assertion_from_facts(items, include_related=True, claim_type=claim_type)
    return audit_assertion(
        assertion,
        page_texts=texts
        if texts is not None
        else {item.page: item.fact.evidence_text for item in items},
    )


def test_literal_object_property_value_unit_page_and_conditions_are_supported():
    """Точное совпадение всех оснований подтверждает сравнение, не выбор правильной стороны."""
    first = observation(
        scope_operating_mode="рабочий", quote="S1 Расход воды 10 л/с рабочий"
    )
    second = observation(
        "12",
        page=2,
        scope_operating_mode="рабочий",
        quote="S1 Расход воды 12 л/с рабочий",
    )
    result = assess(first, second)
    assert result.status == result.disposition == "supported"
    assert all(binding.status == "supported" for binding in result.bindings)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"value_raw": "110"}, "value_not_in_quote"),
        ({"identifier": "S2"}, "object_not_in_quote"),
        ({"source_pages": (2,)}, "physical_page_mismatch"),
        ({"unit_raw": "м³/ч"}, "unit_not_bound_to_quote"),
        ({"scope_operating_mode": "аварийный"}, "condition_not_bound_to_quote"),
        ({"property_name": "Длина"}, "property_not_bound_to_quote"),
    ],
)
def test_wrong_evidence_binding_is_not_proof(change, reason):
    """Число, похожий тег или неподтверждённый режим не создают доказательства."""
    item = observation()
    result = bind_evidence(
        replace(item, fact=replace(item.fact, **change)), {1: item.fact.evidence_text}
    )
    assert result.status != "supported" and result.reason_code == reason


def test_unavailable_page_differs_from_quote_not_found_on_available_page():
    """Недоступный источник не доказывает отсутствие данных в проекте."""
    item = observation()
    assert bind_evidence(item, {}).status == "source_unavailable"
    assert bind_evidence(item, {1: "Другой текст"}).status == "incomplete"


def test_value_from_other_inlet_is_not_accepted_even_when_both_tags_are_in_quote():
    """Общий фрагмент с двумя вводами не разрешает переносить число между ними."""
    item = observation(
        "15",
        identifier="В1-2",
        prop="Длина",
        unit="м",
        quote="В1-1 Длина 15 м; В1-2 Длина 24 м",
    )
    assert (
        bind_evidence(item, {1: item.fact.evidence_text}).reason_code
        == "value_not_in_quote"
    )


@pytest.mark.parametrize(
    "left,right",
    [("Ø110x10", "Ø110*10"), ("Ø110х10", "Ø110×10"), ("Ø110x6,6", "Ø110x6.6")],
)
def test_equivalent_size_notations_do_not_create_violation(left, right):
    """Типоразмер сохраняет исходную запись, а сравнивается без различия знаков умножения."""
    a = observation(left, prop="Диаметр", unit="мм")
    b = observation(right, page=2, prop="Диаметр", unit="мм")
    assert normalized_value(a.fact) == normalized_value(b.fact)
    assert assess(a, b).reason_code == "equivalent_values_or_compatible_descriptions"
    assert a.fact.value_raw == left and b.fact.value_raw == right


def test_nominal_and_outer_diameters_are_different_definitions():
    """DN100 и Ø110 сами по себе не устанавливают несовместимость трубы."""
    result = assess(
        observation("DN100", prop="Диаметр", unit="мм"),
        observation("Ø110", page=2, prop="Диаметр", unit="мм"),
    )
    assert (
        result.disposition == "diagnostic"
        and result.reason_code == "different_parameter_definitions"
    )


@pytest.mark.parametrize(
    "change",
    [
        "identifier",
        "property_name",
        "scope_segment",
        "scope_operating_mode",
        "scope_condition",
    ],
)
def test_different_objects_properties_and_conditions_are_not_violations(change):
    """Различие значений не задаёт обязательной связи независимых объектов и состояний."""
    first = observation(**({change: "рабочий"} if change.startswith("scope_") else {}))
    other = observation("12", page=2)
    second = replace(other, fact=replace(other.fact, **{change: "другой"}))
    assert assess(first, second).disposition == "diagnostic"


def test_wall_difference_without_same_segment_is_question_not_confirmation():
    """Один тег сети не задаёт топологию участков с разной стенкой."""
    result = assess(
        observation("Ø110x10", prop="Диаметр", unit="мм"),
        observation("Ø110x6,6", page=2, prop="Диаметр", unit="мм"),
    )
    assert (
        result.disposition == "question"
        and result.reason_code == "same_physical_segment_not_established"
    )


def test_actual_wall_difference_for_same_explicit_segment_is_supported():
    """Настоящее противоречие остаётся при наличии обоих значений и идентичности участка."""
    first = observation(
        "Ø110x10",
        prop="Диаметр",
        unit="мм",
        scope_segment="У1—У2",
        segment_start="У1",
        segment_end="У2",
        quote="S1 участок У1—У2 диаметр Ø110x10 мм",
    )
    second = observation(
        "Ø110x6,6",
        page=2,
        prop="Диаметр",
        unit="мм",
        scope_segment="У1—У2",
        segment_start="У1",
        segment_end="У2",
        quote="S1 участок У1—У2 диаметр Ø110x6,6 мм",
    )
    assert assess(first, second).status == "supported"


def test_missing_or_invented_second_value_remains_diagnostic():
    """Один скаляр без предметной претензии не создаёт пользовательский вопрос."""
    assert assess(observation()).reason_code == "second_comparison_value_unavailable"
    first = observation()
    second = observation("12", page=2, quote="S1 Расход воды 10 л/с")
    result = assess(first, second)
    assert result.status == "counterevidence" and result.disposition == "diagnostic"


def test_compatible_purposes_and_actual_terminology_error_have_different_outcomes():
    """Совместимые противопожарные описания отклоняются, водоотведение вместо водоснабжения сохраняется."""
    first = observation(
        "противопожарное водоснабжение", prop="Назначение системы", unit=""
    )
    compatible = observation(
        "наружный противопожарный водопровод",
        page=2,
        prop="Назначение системы",
        unit="",
    )
    wrong = observation("водоотведение", page=3, prop="Назначение системы", unit="")
    assert assess(first, compatible).disposition == "diagnostic"
    assert assess(first, wrong).status == "supported"


def test_overlapping_evidence_does_not_merge_independent_pairs():
    """A↔B и B↔C не становятся одним замечанием из-за общей записи B."""
    a, b, c = observation(), observation("12", page=2), observation("14", page=3)
    left = assertion_from_facts((a, b), include_related=True)
    right = assertion_from_facts((b, c), include_related=True)
    assert not same_assertion(left, right)
    assert same_assertion(left, replace(left, observations=(b, a)))


def test_same_explicit_purpose_does_not_depend_on_model_fact_category():
    """Параметр и отношение с тем же явно названным назначением имеют единый инженерный предмет."""
    a = observation("водоснабжение", prop="Назначение системы", unit="")
    b = observation(
        "водоотведение",
        page=2,
        prop="Назначение системы",
        unit="",
        property_type="relationship",
    )
    assert assess(a, b).status == "supported"


def test_unspecified_function_is_not_automatically_incompatible_with_water_supply():
    """Недостаточно конкретное описание назначения оставляет вопрос о смысле, а не нарушение."""
    a = observation("водоснабжение", prop="Назначение системы", unit="")
    b = observation("противопожарная сеть", page=2, prop="Назначение системы", unit="")
    assert assess(a, b).status != "supported"


def test_diameter_definition_is_recovered_from_literal_quote_when_model_kept_only_number():
    """Потеря моделью DN в value_raw не превращает номинальный диаметр в наружный."""
    a = observation("100", prop="Диаметр", unit="мм", quote="S1 Диаметр DN100 мм")
    b = observation(
        "110", page=2, prop="Диаметр", unit="мм", quote="S1 Диаметр Ø110 мм"
    )
    assert assess(a, b).reason_code == "different_parameter_definitions"
    assert normalized_value(a.fact) == normalized_value(
        replace(a.fact, value_raw="DN100")
    )


def test_consumption_and_wastewater_totals_are_not_required_to_be_equal():
    """Одинаковая подпись итога не отменяет разный смысл двух таблиц."""
    a = observation(
        "10", table_id="1", row_label="Итого", table_title="Водопотребление"
    )
    b = observation(
        "12", page=2, table_id="2", row_label="Итого", table_title="Водоотведение"
    )
    result = assess(a, b)
    assert (
        result.disposition == "diagnostic"
        and result.reason_code == "different_parameter_roles"
    )


def test_single_visual_value_keeps_binding_diagnostic_without_creating_claim():
    """Неоднозначная привязка одного числа сохраняется для диагностики, не порождая замечание."""
    first = observation(
        "Ø110x10",
        prop="Диаметр",
        unit="мм",
        quote="Диаметр Ø110x10 мм",
        visual_regions=[
            {"x_min": 10, "y_min": 20, "x_max": 100, "y_max": 60, "confidence": 0.9}
        ],
    )
    result = assess(first, texts={1: "S1\nДиаметр Ø110x10 мм"})
    assert (
        result.disposition == "diagnostic"
        and result.bindings[0].reason_code == "object_binding_ambiguous"
    )


def test_legacy_purpose_assertion_keeps_meaning_after_property_category_normalization():
    """Старый property_key категории parameter читается без потери корректного предмета."""
    item = observation("водоснабжение", prop="Назначение системы", unit="")
    original = assertion_from_facts((item,)).as_origin()
    original["property_key"] = "parameter:system_purpose"
    restored = read_assertion((original,))
    assert restored.property_key == "relationship:system_purpose"
    assert restored.observations == (item,)


def test_length_label_on_drawing_is_grounded_but_does_not_identify_same_segment():
    """Подпись L читается как длина, но разные L одного ввода ещё не доказывают нарушение."""
    a = observation("15,00", prop="Длина", unit="м", quote="S1 L=15,00м")
    b = observation("22,50", page=2, prop="Длина", unit="м", quote="S1 L=22,50м")
    assert assess(a, b).reason_code == "same_physical_segment_not_established"


def test_meter_is_not_matched_as_part_of_millimeter_unit():
    """Число 10 мм в цитате не подтверждает принятое моделью значение 10 м."""
    item = observation("10", prop="Длина", unit="м", quote="S1 Длина 10 мм")
    assert (
        bind_evidence(item, {1: item.fact.evidence_text}).reason_code
        == "unit_not_bound_to_quote"
    )
