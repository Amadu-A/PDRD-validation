# services/analysis-service/src/pdrd_analysis_service/domain/document_context.py

"""Факты проверяемого PDF и консервативное межстраничное сопоставление."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class DocumentFact:
    """Атомарное утверждение документа с исходной формулировкой."""

    kind: str
    subject_type: str
    subject_name: str
    identifier: str
    property_type: str
    property_name: str
    value_raw: str
    unit_raw: str
    scope_system: str
    scope_location: str
    scope_segment: str
    scope_operating_mode: str
    scope_condition: str
    relation: str
    table_title: str
    table_id: str
    row_label: str
    column_label: str
    continuation_marker: str
    evidence_text: str
    visual_regions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class LocatedDocumentFact:
    """Факт вместе с физической страницей исходного PDF."""

    page: int
    index: int
    fact: DocumentFact

    @property
    def fact_id(self) -> str:
        """Возвращает стабильный в пределах анализа идентификатор факта."""
        return f"p{self.page:04d}-f{self.index:04d}"


@dataclass(frozen=True, slots=True)
class ConflictGroup:
    """Несколько несовместимых значений одного объекта и свойства."""

    facts: tuple[LocatedDocumentFact, ...]
    subject_key: str
    property_key: str
    mismatch_type: str


_SPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_UNIT_FACTORS: dict[str, tuple[str, Decimal]] = {
    "°c": ("temperature_c", Decimal("1")),
    "град.c": ("temperature_c", Decimal("1")),
    "град.c.": ("temperature_c", Decimal("1")),
    "c": ("temperature_c", Decimal("1")),
    "pa": ("pressure_pa", Decimal("1")),
    "па": ("pressure_pa", Decimal("1")),
    "kpa": ("pressure_pa", Decimal("1000")),
    "кпа": ("pressure_pa", Decimal("1000")),
    "mpa": ("pressure_pa", Decimal("1000000")),
    "мпа": ("pressure_pa", Decimal("1000000")),
    "w": ("power_w", Decimal("1")),
    "вт": ("power_w", Decimal("1")),
    "kw": ("power_w", Decimal("1000")),
    "квт": ("power_w", Decimal("1000")),
    "mm": ("length_mm", Decimal("1")),
    "мм": ("length_mm", Decimal("1")),
    "m": ("length_mm", Decimal("1000")),
    "м": ("length_mm", Decimal("1000")),
    "m3/h": ("flow_m3h", Decimal("1")),
    "м3/ч": ("flow_m3h", Decimal("1")),
    "м³/ч": ("flow_m3h", Decimal("1")),
    "l/s": ("flow_m3h", Decimal("3.6")),
    "л/с": ("flow_m3h", Decimal("3.6")),
    "шт": ("count", Decimal("1")),
    "шт.": ("count", Decimal("1")),
}

_PROPERTY_ALIASES = {
    "расчетная температура наружного воздуха": "design_outdoor_temperature",
    "температура наружного воздуха расчетная": "design_outdoor_temperature",
}


def normalize_label(value: str) -> str:
    """Нормализует пробелы, регистр и варианты написания."""
    return _SPACE.sub(" ", value.casefold().replace("ё", "е").strip())


def subject_key(fact: DocumentFact) -> str:
    """Связывает объект только по явному идентификатору и типу."""
    identifier = normalize_label(fact.identifier)
    object_type = normalize_label(fact.subject_type)
    if not identifier or not object_type:
        return ""
    return f"{object_type}:{identifier}"


def property_key(fact: DocumentFact) -> str:
    """Отличает смысл параметра от одной лишь физической размерности."""
    name = normalize_label(fact.property_name)
    kind = normalize_label(fact.property_type or fact.kind)
    if not name or not kind:
        return ""
    return f"{kind}:{_PROPERTY_ALIASES.get(name, name)}"


def scope_key(fact: DocumentFact) -> tuple[str, ...]:
    """Возвращает условия для безопасного автоматического сопоставления."""
    return tuple(
        normalize_label(value)
        for value in (
            fact.scope_system,
            fact.scope_location,
            fact.scope_segment,
            fact.scope_operating_mode,
            fact.scope_condition,
        )
    )


def normalized_value(fact: DocumentFact) -> tuple[str, str] | None:
    """Сравнивает единицы без подмены исходного значения."""
    raw = normalize_label(fact.value_raw).replace(" ", "")
    unit = normalize_label(fact.unit_raw).replace(" ", "")
    if not raw:
        return None
    if raw.startswith("dn"):
        return "nominal_diameter", raw
    if _NUMBER.fullmatch(raw):
        try:
            number = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            return None
        if unit:
            converted = _UNIT_FACTORS.get(unit)
            if converted is None:
                return None
            dimension, factor = converted
            return dimension, str((number * factor).normalize())
        return "number", str(number.normalize())
    if unit:
        return None
    return "text", raw


def discover_conflicts(
    facts: tuple[LocatedDocumentFact, ...],
    *,
    max_candidates: int,
) -> tuple[ConflictGroup, ...]:
    """Ищет ограниченное число групп без попарного VLM-сравнения страниц."""
    groups: dict[tuple[str, str, tuple[str, ...]], list[LocatedDocumentFact]] = {}
    for located in facts:
        fact = located.fact
        subject = subject_key(fact)
        prop = property_key(fact)
        value = normalized_value(fact)
        if not subject or not prop or value is None:
            continue
        groups.setdefault((subject, prop, scope_key(fact)), []).append(located)

    conflicts: list[ConflictGroup] = []
    for (subject, prop, _scope), members in groups.items():
        if len({member.page for member in members}) < 2:
            continue
        values = {normalized_value(member.fact) for member in members}
        dimensions = {value[0] for value in values if value is not None}
        if len(dimensions) != 1 or len(values) < 2:
            continue
        kind = normalize_label(members[0].fact.kind)
        prop_type = normalize_label(members[0].fact.property_type)
        mismatch_type = {
            "quantity": "quantity_mismatch",
            "equipment_model": "equipment_model_mismatch",
            "material": "material_mismatch",
            "room_property": "room_property_mismatch",
            "relationship": "relationship_mismatch",
        }.get(kind, f"{prop_type}_mismatch" if prop_type else "value_mismatch")
        conflicts.append(
            ConflictGroup(
                facts=tuple(members),
                subject_key=subject,
                property_key=prop,
                mismatch_type=mismatch_type,
            )
        )
        if len(conflicts) >= max_candidates:
            break
    return tuple(conflicts)


def fact_search_text(located: LocatedDocumentFact) -> str:
    """Строит короткое индексируемое представление D-факта."""
    fact = located.fact
    return (
        f"{located.fact_id} страница {located.page}; "
        f"{fact.subject_type} {fact.subject_name} {fact.identifier}; "
        f"{fact.property_type} {fact.property_name}: {fact.value_raw} {fact.unit_raw}; "
        f"система {fact.scope_system}; участок {fact.scope_segment}; "
        f"режим {fact.scope_operating_mode}; {fact.evidence_text}"
    ).strip()


_FACT_TEXT_FIELDS = (
    "kind",
    "subject_type",
    "subject_name",
    "identifier",
    "property_type",
    "property_name",
    "value_raw",
    "unit_raw",
    "scope_system",
    "scope_location",
    "scope_segment",
    "scope_operating_mode",
    "scope_condition",
    "relation",
    "table_title",
    "table_id",
    "row_label",
    "column_label",
    "continuation_marker",
    "evidence_text",
)


def document_fact_from_mapping(raw: object) -> DocumentFact | None:
    """Принимает только атомарный факт с проверяемым текстовым основанием."""
    if not isinstance(raw, dict):
        return None
    values = {
        field: str(raw.get(field, "") or "").strip() for field in _FACT_TEXT_FIELDS
    }
    if not values["kind"] or not values["evidence_text"]:
        return None
    regions = raw.get("visual_regions", [])
    safe_regions: list[dict[str, Any]] = []
    if isinstance(regions, list):
        for region in regions[:2]:
            if not isinstance(region, dict):
                continue
            try:
                box = {
                    key: int(region[key])
                    for key in ("x_min", "y_min", "x_max", "y_max")
                }
                confidence = float(region.get("confidence", 0.0))
            except (KeyError, TypeError, ValueError):
                continue
            if (
                0 <= box["x_min"] < box["x_max"] <= 1000
                and 0 <= box["y_min"] < box["y_max"] <= 1000
                and 0 <= confidence <= 1
            ):
                safe_regions.append({**box, "confidence": confidence})
    return DocumentFact(**values, visual_regions=tuple(safe_regions))
