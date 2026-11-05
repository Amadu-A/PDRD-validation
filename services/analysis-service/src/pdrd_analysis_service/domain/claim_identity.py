# services/analysis-service/src/pdrd_analysis_service/domain/claim_identity.py

"""Тождество инженерной претензии между маршрутами без сравнения свободного текста."""

from pdrd_analysis_service.domain.document_context import (
    normalize_label,
    normalized_value,
    scope_key,
)
from pdrd_analysis_service.domain.engineering_assertion import read_assertion
from pdrd_analysis_service.domain.equipment import equipment_property_name
from pdrd_analysis_service.domain.physical_identity import comparison_identity


def same_engineering_claim(left, right) -> bool:
    """Требует одинакового предмета, свойства, значений, условий и общего доказательства.

    Близость формулировок и совпадение одного тега не достаточны. Неполное
    обоснование и отдельные нормативные обязанности остаются независимыми.
    """
    first, second = (
        read_assertion(left.origin_assertions),
        read_assertion(right.origin_assertions),
    )
    if first is None or second is None:
        return _same_equipment_claim(left, right) or _same_equipment_claim(right, left)
    if first.claim_type != "contradiction" or second.claim_type != first.claim_type:
        return False
    if first.object_key != second.object_key or first.conditions != second.conditions:
        return False
    if comparison_identity((*first.observations, *second.observations)) not in {
        "not_required",
        "same_physical_object",
    }:
        return False

    def propositions(assertion):
        """Канонизирует только поддерживаемые предметные параметры и их единицы."""
        return {
            (
                equipment_property_name(item.fact.property_name)
                or assertion.property_key,
                normalized_value(item.fact),
                scope_key(item.fact),
            )
            for item in assertion.observations
        }

    values = propositions(first)
    if (
        len(values) < 2
        or any(value[1] is None for value in values)
        or values != propositions(second)
    ):
        return False
    return any(
        a.page == b.page
        and normalized_value(a.fact) == normalized_value(b.fact)
        and normalize_label(a.fact.evidence_text)
        == normalize_label(b.fact.evidence_text)
        and a.fact.evidence_text.strip()
        for a in first.observations
        for b in second.observations
    )


def _same_equipment_claim(equipment, document) -> bool:
    """Связывает паспортное и документное сравнения только при общем исходном факте."""
    details = equipment.equipment_details or {}
    assertion = read_assertion(document.origin_assertions)
    if not details or assertion is None or assertion.claim_type != "contradiction":
        return False
    if details.get("applicability_status") != "applicable" or not details.get(
        "object_ref"
    ):
        return False
    facts = assertion.observations
    if any(
        normalize_label(item.fact.identifier) != normalize_label(details["object_ref"])
        for item in facts
    ):
        return False
    if any(
        equipment_property_name(item.fact.property_name) != details.get("property_name")
        for item in facts
    ):
        return False
    # Локальные параметры требуют доказанного физического объекта, а не одной сети.
    if comparison_identity(facts) not in {"not_required", "same_physical_object"}:
        return False
    expected_scope = tuple(
        normalize_label(str(details.get(key) or "")) for key in ("mode", "condition")
    )
    if any(
        (
            normalize_label(item.fact.scope_operating_mode),
            normalize_label(item.fact.scope_condition),
        )
        != expected_scope
        or item.fact.scope_location
        or item.fact.scope_segment
        for item in facts
    ):
        return False
    from dataclasses import replace

    template = facts[0].fact
    expected = {
        normalized_value(
            replace(
                template,
                value_raw=str(details.get(value) or ""),
                unit_raw=str(details.get(unit) or ""),
            )
        )
        for value, unit in (
            ("project_value", "project_unit"),
            ("manufacturer_value", "manufacturer_unit"),
        )
    }
    if (
        None in expected
        or len(expected) != 2
        or expected != {normalized_value(item.fact) for item in facts}
    ):
        return False
    return any(
        item.page == equipment.page
        and item.fact.evidence_text.strip()
        and normalize_label(item.fact.evidence_text)
        == normalize_label(equipment.evidence)
        for item in facts
    )
