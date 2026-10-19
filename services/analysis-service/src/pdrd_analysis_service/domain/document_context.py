# services/analysis-service/src/pdrd_analysis_service/domain/document_context.py

"""Факты проверяемого PDF и консервативное межстраничное сопоставление."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from pdrd_analysis_service.domain.engineering_claims import extract_quantities


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
    source_item_id: str = ""
    source_row_ids: tuple[str, ...] = ()
    source_pages: tuple[int, ...] = ()
    source_regions: tuple[dict[str, Any], ...] = ()
    extraction_method: str = ""
    calculation_group: str = ""
    calculation_rule: str = ""
    calculation_evidence: str = ""
    column_binding: str = ""
    object_kind: str = ""
    value_role: str = ""
    segment_start: str = ""
    segment_end: str = ""
    geometry_binding: dict[str, Any] | None = None
    source_fragments: tuple[dict[str, Any], ...] = ()


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
    "pipe_diameter": "Диаметр",
    "casing_diameter": "Диаметр",
    "dry_pipe_diameter": "Диаметр",
    "pipe_length": "Длина",
    "casing_length": "Длина",
    "connection_elevation": "Абсолютная отметка",
    "system_type": "system_purpose",
    "расчетная температура наружного воздуха": "design_outdoor_temperature",
    "температура наружного воздуха расчетная": "design_outdoor_temperature",
    "назначение системы": "system_purpose",
    "наименование системы": "system_purpose",
    "расход воды": "water_flow",
    "расчетный расход воды": "water_flow",
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
    if _PROPERTY_ALIASES.get(name) == "system_purpose":
        return "relationship:system_purpose"
    return f"{kind}:{normalize_label(_PROPERTY_ALIASES.get(name, name))}"


def scope_key(fact: DocumentFact) -> tuple[str, ...]:
    """Возвращает условия для безопасного автоматического сопоставления."""
    scopes = tuple(
        normalize_label(value)
        for value in (
            fact.scope_system,
            fact.scope_location,
            fact.scope_segment,
            fact.scope_operating_mode,
            fact.scope_condition,
        )
    )
    if scopes[0] == normalize_label(fact.identifier):
        scopes = ("", *scopes[1:])
    # Разные карты ОСР описывают разные расчётные условия даже при пропущенном scope.
    seismic_map = re.search(
        r"\bоср[-\s\d]*(?:[-\s])?([abcавс])\b", fact.evidence_text, re.I
    )
    return (
        *scopes,
        seismic_map[1].casefold().translate(str.maketrans("авс", "abc"))
        if seismic_map
        else "",
        normalize_label(fact.row_label) if fact.table_id else "",
    )


def normalized_value(fact: DocumentFact) -> tuple[str, str] | None:
    """Сравнивает единицы без подмены исходного значения."""
    raw = normalize_label(fact.value_raw).replace(" ", "")
    # Знаки умножения в типоразмере не меняют наружный диаметр и стенку.
    raw = re.sub(r"(?<=\d)[xх×*](?=\d)", "x", raw).replace(",", ".")
    unit = normalize_label(fact.unit_raw).replace(" ", "")
    inline = re.fullmatch(
        r"(?P<number>[+-]?\d+(?:\.\d+)?)(?P<unit>м[³3]/(?:ч|сут)|л/с|м/с|мм|м[³3]|м)",
        raw,
    )
    if inline and (not unit or unit == inline["unit"]):
        raw, unit = inline["number"], inline["unit"]
    if not raw or re.search(r"\?|неразбор|нечита|неясн", raw):
        return None
    nominal = re.fullmatch(r"(?:dn|ду)(\d+(?:\.\d+)?)", raw)
    if nominal:
        return "nominal_diameter", str(Decimal(nominal[1]).normalize())
    outer = re.fullmatch(r"[ø⌀ф](\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?)", raw)
    if outer:
        return "outer_diameter", "x".join(
            str(Decimal(value).normalize()) for value in outer[1].split("x")
        )
    prop = normalize_label(fact.property_name)
    if "диаметр" in prop or "diameter" in prop:
        value = str(Decimal(raw).normalize()) if _NUMBER.fullmatch(raw) else raw
        if re.search(r"условн|номиналь|\b(?:dn|ду)\b", prop):
            return "nominal_diameter", value
        if "наружн" in prop:
            return "outer_diameter", value
        if _NUMBER.fullmatch(raw):
            literal_number = re.escape(raw).replace(r"\.", "[.,]") + r"(?![\d.,])"
            if re.search(r"\b(?:dn|ду)\s*" + literal_number, fact.evidence_text, re.I):
                return "nominal_diameter", value
            if re.search(r"[ø⌀ф]\s*" + literal_number, fact.evidence_text, re.I):
                return "outer_diameter", value
    if _PROPERTY_ALIASES.get(prop) == "system_purpose":
        for pattern, purpose in (
            (r"водоотвед|канализац", "water_disposal"),
            (r"водоснабж|водопровод", "water_supply"),
            (r"электроснабж", "electric_supply"),
            (r"теплоснабж", "heat_supply"),
        ):
            if re.search(pattern, raw):
                return "text", purpose
        return None
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
    require_physical_identity: bool = False,
) -> tuple[ConflictGroup, ...]:
    """Ищет ограниченное число групп без попарного VLM-сравнения страниц."""
    groups: dict[tuple[str, str, tuple[str, ...]], list[LocatedDocumentFact]] = {}
    for located in facts:
        fact = located.fact
        # Назначение пожарного расхода ещё не идентифицирует защищаемый объект.
        # Сам факт остаётся в реестре и проверке обоснования, но не доказывает равенство расходов разных объектов.
        if fact.relation == "accepted_fire_demand" and not (
            fact.scope_system or fact.scope_location
        ):
            continue
        if _printed_sheet_number(fact) is not None:
            continue
        subject = subject_key(fact)
        prop = property_key(fact)
        value = normalized_value(fact)
        if not subject or not prop or value is None:
            continue
        physical = None
        if require_physical_identity:
            from pdrd_analysis_service.domain.physical_identity import (
                geometric_property,
                physical_key,
            )

            if geometric_property(fact):
                physical = physical_key(located)
                if physical is None:
                    continue
        groups.setdefault(
            (subject, prop, (*scope_key(fact), repr(physical))), []
        ).append(located)

    conflicts: list[ConflictGroup] = []
    for (subject, prop, _scope), members in groups.items():
        same_row = len(
            {(member.fact.table_id, member.fact.row_label) for member in members}
        ) == 1 and all(
            member.fact.table_id and member.fact.row_label for member in members
        )
        units = {normalize_label(member.fact.unit_raw) for member in members}
        row_conversion = same_row and len(units) > 1
        if len({member.page for member in members}) < 2 and not row_conversion:
            continue
        values = {normalized_value(member.fact) for member in members}
        dimensions = {value[0] for value in values if value is not None}
        if len(dimensions) != 1 or len(values) < 2:
            continue
        quantities = [
            extract_quantities(f"{member.fact.value_raw} {member.fact.unit_raw}")
            for member in members
        ]
        if all(len(value) == 1 for value in quantities):
            first = quantities[0][0]
            if all(
                value[0].dimension == first.dimension
                and abs(value[0].value - first.value)
                <= value[0].tolerance + first.tolerance
                for value in quantities
            ):
                continue
        kind = normalize_label(members[0].fact.kind)
        prop_type = normalize_label(members[0].fact.property_type)
        mismatch_type = (
            "unit_equivalence"
            if row_conversion
            else {
                "quantity": "quantity_mismatch",
                "equipment_model": "equipment_model_mismatch",
                "material": "material_mismatch",
                "room_property": "room_property_mismatch",
                "relationship": "relationship_mismatch",
            }.get(kind, f"{prop_type}_mismatch" if prop_type else "value_mismatch")
        )
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
    remaining = max_candidates - len(conflicts)
    if remaining > 0:
        conflicts.extend(_relationship_conflicts(facts)[:remaining])
    return tuple(conflicts)


def _printed_sheet_number(fact: DocumentFact) -> int | None:
    """Читает явно извлечённое проектное поле, не используя физический индекс PDF как номер листа."""
    if (
        fact.subject_type != "drawing_document"
        or fact.property_type != "sheet_number"
        or not fact.identifier
    ):
        return None
    match = re.fullmatch(r"(?:Лист\s*)?(\d+)", fact.value_raw.strip(), re.I)
    return int(match[1]) if match is not None else None


def _relationship_conflicts(
    facts: tuple[LocatedDocumentFact, ...],
) -> list[ConflictGroup]:
    """Сопоставляет явный состав и печатную последовательность; неполный перечень не доказывает недостачу."""
    result = []
    memberships: dict[tuple, dict[str, list[LocatedDocumentFact]]] = {}
    sheets: dict[tuple, dict[int, list[LocatedDocumentFact]]] = {}
    inventories: dict[tuple, list[LocatedDocumentFact]] = {}
    for located in facts:
        fact = located.fact
        if (
            fact.relation == "member_of"
            and fact.identifier
            and fact.scope_system
            and fact.subject_type
        ):
            key = (
                normalize_label(fact.scope_system),
                normalize_label(fact.subject_type),
                scope_key(fact)[1:6],
            )
            memberships.setdefault(key, {}).setdefault(
                normalize_label(fact.identifier), []
            ).append(located)
        if _printed_sheet_number(fact) is not None:
            sheets.setdefault((subject_key(fact), scope_key(fact)), {}).setdefault(
                located.page, []
            ).append(located)
    for located in facts:
        fact = located.fact
        if (
            fact.kind != "quantity"
            or fact.property_type != "inventory_count"
            or not fact.identifier
        ):
            continue
        if not re.fullmatch(r"\d+", fact.value_raw.strip()):
            continue
        if fact.scope_system and normalize_label(fact.scope_system) != normalize_label(
            fact.identifier
        ):
            continue
        key = (
            normalize_label(fact.identifier),
            normalize_label(fact.subject_type),
            scope_key(fact)[1:6],
        )
        members = memberships.get(key, {})
        # Только избыточный явно связанный состав доказывается неполным наблюдением.
        if len(members) > int(fact.value_raw):
            inventories.setdefault((key, int(fact.value_raw)), []).append(located)
    for (key, _count), declarations in inventories.items():
        members = memberships[key]
        result.append(
            ConflictGroup(
                (
                    *declarations,
                    *(item for group in members.values() for item in group),
                ),
                subject_key(declarations[0].fact),
                "inventory_count",
                "inventory_composition",
            )
        )
    for (subject, _scope), by_page in sheets.items():
        unambiguous = [
            (page, members)
            for page, members in sorted(by_page.items())
            if len({_printed_sheet_number(item.fact) for item in members}) == 1
        ]
        for (first_page, first), (second_page, second) in pairwise(unambiguous):
            if (
                second_page == first_page + 1
                and _printed_sheet_number(second[0].fact)
                != _printed_sheet_number(first[0].fact) + 1
            ):
                result.append(
                    ConflictGroup(
                        (*first, *second), subject, "sheet_number", "sheet_sequence"
                    )
                )
    return result


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
    if isinstance(regions, (list, tuple)):
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
    return DocumentFact(
        **values,
        visual_regions=tuple(safe_regions),
        source_item_id=str(raw.get("source_item_id") or ""),
        source_row_ids=tuple(
            value
            for value in _provenance_values(raw, "source_row_ids")
            if isinstance(value, str)
        ),
        source_pages=tuple(
            value
            for value in _provenance_values(raw, "source_pages")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        ),
        source_regions=tuple(
            dict(region)
            for region in _provenance_values(raw, "source_regions")
            if isinstance(region, dict)
        ),
        extraction_method=str(raw.get("extraction_method") or ""),
        object_kind=str(raw.get("object_kind") or ""),
        value_role=str(raw.get("value_role") or ""),
        segment_start=str(raw.get("segment_start") or ""),
        segment_end=str(raw.get("segment_end") or ""),
        geometry_binding=dict(raw["geometry_binding"])
        if isinstance(raw.get("geometry_binding"), dict)
        else None,
        source_fragments=tuple(
            dict(part)
            for part in _provenance_values(raw, "source_fragments")
            if isinstance(part, dict)
        ),
        **{
            key: str(raw.get(key) or "")
            for key in (
                "calculation_group",
                "calculation_rule",
                "calculation_evidence",
                "column_binding",
            )
        },
    )


def _provenance_values(raw: dict, key: str) -> tuple | list:
    """Не допускает обхода строки или None как массива происхождения."""
    value = raw.get(key)
    return value if isinstance(value, (list, tuple)) else ()
