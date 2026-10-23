# services/analysis-service/src/pdrd_analysis_service/domain/engineering_checks.py

"""Программная проверка решений: основания, арифметика, состав и расчётные зависимости."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

from pdrd_analysis_service.domain.design_decisions import DecisionRegistry, stable_id
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    normalize_label,
    normalized_value,
    property_key,
    scope_key,
)
from pdrd_analysis_service.domain.engineering_assertion import engineering_object_key
from pdrd_analysis_service.domain.engineering_calculations import (
    Scalar,
    arithmetic,
    comparison_tolerance,
    scalar,
)
from pdrd_analysis_service.domain.project_dependencies import (
    explicit_project_dependencies,
)


@dataclass(frozen=True, slots=True)
class EngineeringCheck:
    """Проверяемый результат, который не зависит от формулировки замечания моделью."""

    rule: str
    status: str
    object_key: str
    property_key: str
    observations: tuple[LocatedDocumentFact, ...]
    reason_code: str
    calculation: dict | None = None
    missing_data: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()

    @property
    def check_id(self) -> str:
        """Одинаковые проверки локального и полного документа имеют один идентификатор."""
        return stable_id(
            "check-",
            (
                self.rule,
                self.object_key,
                self.property_key,
                self.source_ids,
                tuple(sorted(item.fact_id for item in self.observations)),
                *(
                    (
                        (self.calculation or {}).get("requirement_id"),
                        (self.calculation or {}).get("normative_requirement_id"),
                    )
                    if self.rule.startswith("requirement_plan")
                    else ()
                ),
            ),
        )

    def as_dict(self) -> dict:
        """Сохраняет проверенные данные и причину ограничения, включая отвергнутые гипотезы."""
        return {"check_id": self.check_id, **asdict(self)}


def evaluate_decisions(
    registry: DecisionRegistry, *, page_texts: dict[int, str]
) -> tuple[EngineeringCheck, ...]:
    """Проверяет уже прочитанные данные, сохраняя отдельный результат для каждой зависимости."""
    grounded = tuple(
        item.observation
        for item in registry.evidence
        if program_evidence_eligible(item)
    )
    results = []
    for decision in registry.decisions:
        observations = tuple(item.observation for item in decision.observations)
        results.append(
            EngineeringCheck(
                "justification_chain",
                "not_evaluable",
                decision.object_key,
                decision.decision_type,
                observations,
                "unresolved_design_dependencies",
                missing_data=decision.unresolved_dependencies,
            )
        )
    references: dict[tuple, list[LocatedDocumentFact]] = defaultdict(list)
    for item in grounded:
        result = _arithmetic_check(item)
        if result is not None:
            results.append(result)
        if item.fact.property_type in {"calculation_reference", "document_reference"}:
            key = (
                normalize_label(item.fact.value_raw),
                None if registry.document_context_enabled else item.page,
            )
            references[key].append(item)
    for mentions in references.values():
        check = resolve_reference(
            mentions[0], grounded, page_texts, registry.document_context_enabled
        )
        results.append(
            replace(check, observations=tuple(mentions) + check.observations[1:])
        )
    topics: dict[tuple, list[LocatedDocumentFact]] = defaultdict(list)
    for item in grounded:
        if item.fact.relation == "topic_reference":
            key = (
                engineering_object_key(item.fact),
                item.page,
            )
            topics[key].append(item)
    for mentions in topics.values():
        targets = {normalize_label(item.fact.value_raw) for item in mentions}
        if len(targets) > 1:
            results.append(
                EngineeringCheck(
                    "topic_reference_consistency",
                    "confirmed",
                    engineering_object_key(mentions[0].fact),
                    "topic_reference",
                    tuple(mentions),
                    "same_topic_different_reference_targets",
                    {"targets": sorted(targets)},
                )
            )
    groups: dict[tuple, list[LocatedDocumentFact]] = defaultdict(list)
    local_identities = {
        item.evidence_id
        for decision in registry.decisions
        if dict(decision.completeness).get("identity") == "ambiguous"
        for item in decision.observations
    }
    for item in grounded:
        key = (engineering_object_key(item.fact), scope_key(item.fact)[:6])
        if not registry.document_context_enabled or item.fact_id in local_identities:
            key = (*key, item.page)
        if key[0]:
            groups[key].append(item)
    for members in groups.values():
        results.extend(_head_balance(tuple(members)))
        results.extend(_fire_reserve(tuple(members)))
        results.extend(_procurement(tuple(members)))
        results.extend(_hydraulics(tuple(members)))
    tables: dict[tuple, list[LocatedDocumentFact]] = defaultdict(list)
    for item in grounded:
        if item.fact.table_id:
            scope = scope_key(item.fact)
            key = (
                normalize_label(item.fact.table_id),
                *scope[:3],
                *scope[4:6],
                item.fact.calculation_group,
            )
            if not registry.document_context_enabled:
                key = (*key, item.page)
            tables[key].append(item)
    for members in tables.values():
        results.extend(_table_sums(tuple(members)))
    if registry.document_context_enabled:
        results.extend(_design_basis_questions(grounded, page_texts))
        results.extend(_supply_length_questions(registry, grounded))
    return tuple(results)


def _supply_length_questions(registry, grounded):
    """Увязывает протяжённость и количество трубы вопросом о границах и запасе.

    Графическая строка остаётся визуальным чтением. Она не становится доказанным
    общим участком и не используется для автоматического вывода о недостатке.
    """

    def question_system(fact, value):
        """Сближает B/В только в вопросе о водопроводе, сохраняя исходное чтение."""
        tag = normalize_label(value)
        if re.fullmatch(r"b\d+(?:-\d+)?", tag) and re.search(
            r"водопровод|водоснабжен", fact.evidence_text, re.I
        ):
            return "в" + tag[1:]
        return tag

    groups = defaultdict(list)
    for evidence in registry.evidence:
        item = evidence.observation
        fact = item.fact
        if fact.value_role == "network_total_length" and item in grounded:
            groups[question_system(fact, fact.identifier)].append(item)
        elif (
            fact.value_role == "supply_length"
            and fact.extraction_method == "vlm_specification_row"
        ):
            from pdrd_analysis_service.domain.visual_specification import (
                visual_specification_facts,
            )

            rows = [
                region["row"]
                for region in fact.source_regions
                if region.get("role") == "specification_row"
                and isinstance(region.get("row"), dict)
            ]
            source_groups = [
                {
                    "system": row["system"],
                    "header": row["system_header"],
                    "bbox": [
                        row["header_bbox"][key]
                        for key in ("x_min", "y_min", "x_max", "y_max")
                    ],
                    "rows": [row["columns"]],
                }
                for row in rows
                if isinstance(row.get("columns"), dict)
            ]
            if fact in visual_specification_facts(source_groups, item.page):
                groups[question_system(fact, fact.scope_system)].append(item)
    results = []
    for system, observations in groups.items():
        lengths = [
            item
            for item in observations
            if item.fact.value_role == "network_total_length"
        ]
        supplied = [
            item for item in observations if item.fact.value_role == "supply_length"
        ]
        if not lengths or not supplied:
            continue
        if any(
            item.fact.unit_raw != "м"
            or not re.fullmatch(r"\d+(?:[.,]\d+)?", item.fact.value_raw)
            for item in observations
        ):
            continue
        if (
            len(
                {
                    Decimal(item.fact.value_raw.replace(",", "."))
                    for item in observations
                }
            )
            < 2
        ):
            continue
        results.append(
            EngineeringCheck(
                "network_supply_scope",
                "needs_review",
                "system:" + system,
                "network_and_supply_length",
                tuple(observations),
                "supply_scope_and_allowance_unverified",
                {
                    "network": [
                        f"стр. {item.page}: {item.fact.value_raw} м" for item in lengths
                    ],
                    "supply": [
                        f"стр. {item.page}: {item.fact.value_raw} м"
                        for item in supplied
                    ],
                },
                missing_data=(
                    "supply_allowance",
                    "network_boundaries",
                    "specification_row_to_segments",
                )
                + (
                    ("system_tag_reading",)
                    if len(
                        {
                            normalize_label(
                                item.fact.scope_system or item.fact.identifier
                            )
                            for item in observations
                        }
                    )
                    > 1
                    else ()
                ),
            )
        )
    return tuple(results)


def _design_basis_questions(
    facts: tuple[LocatedDocumentFact, ...], page_texts: dict[int, str]
) -> tuple[EngineeringCheck, ...]:
    """Проверяет конкретные зависимости принятых решений, не объявляя пробелы нарушением."""
    result = []
    seen = set()
    for item in facts:
        fact = item.fact
        fire = _fact_kind(fact) == "design_fire_flow" and bool(
            re.search(r"пожар", fact.evidence_text, re.I)
        )
        pipe = fact.relation == "accepted_pipe_choice"
        storage = fact.relation == "accepted_storage_choice"
        protection = fact.relation == "accepted_protection_solution"
        if not (fire or pipe or storage or protection):
            continue
        related = tuple(
            other
            for other in facts
            if engineering_object_key(other.fact) == engineering_object_key(fact)
        )
        if pipe and not any(
            property_key(other.fact).endswith(":system_purpose") for other in related
        ):
            continue
        dependency = (
            "fire_flow_basis"
            if fire
            else "fire_storage_basis"
            if storage
            else "protected_object_basis"
            if protection
            else "pipe_selection_basis"
        )
        key = (
            engineering_object_key(fact),
            dependency,
            scope_key(fact),
            fact.value_raw,
            fact.unit_raw,
        )
        if key in seen:
            continue
        seen.add(key)
        from pdrd_analysis_service.domain.physical_identity import physical_key

        segment = physical_key(item) if pipe else None
        comparable = tuple(
            other
            for other in related
            if scope_key(other.fact)[:6] == scope_key(fact)[:6]
            and (not pipe or (segment is not None and physical_key(other) == segment))
        )
        kinds = {_fact_kind(other.fact) for other in comparable}
        required = (
            (
                "useful_fire_volume",
                "design_fire_flow",
                "fire_duration",
                "guaranteed_refill_flow",
                "design_input",
                "calculation_reference",
            )
            if storage
            else (
                "design_input",
                "requirement_reference",
                "calculation_reference",
                "document_reference",
            )
            if protection
            else ("design_input", "requirement_reference")
            if fire
            else ("design_flow", "internal_diameter", "flow_velocity", "head_loss")
        )
        # Наличие связанного расчёта ограничивает автоматический вывод: его проверяет обычная цепочка.
        if not (storage or protection) and any(
            other.fact.property_type == "calculation_reference" for other in comparable
        ):
            continue
        missing = tuple(kind for kind in required if kind not in kinds)
        if pipe and segment is None:
            missing += ("calculation_segment",)
        if pipe and not fact.scope_operating_mode:
            missing += ("operating_mode",)
        if not missing:
            continue
        observations = (item, *(other for other in related if other != item))
        from pdrd_analysis_service.domain.engineering_facts import (
            counted_storage_components,
        )

        result.append(
            EngineeringCheck(
                "design_basis_dependency",
                "needs_review",
                engineering_object_key(fact),
                dependency,
                observations,
                "specific_design_prerequisites_not_observed",
                {
                    "accepted": fact.value_raw + " " + fact.unit_raw,
                    "dependency": dependency,
                    "checked_pages": sorted(page_texts),
                    "missing": list(missing),
                    **(
                        {
                            "storage_composition": list(
                                counted_storage_components(fact.evidence_text)
                            )
                        }
                        if storage
                        else {}
                    ),
                },
                missing_data=missing,
            )
        )
    return tuple(result)


def _arithmetic_check(item: LocatedDocumentFact) -> EngineeringCheck | None:
    fact = item.fact
    if fact.property_type != "arithmetic_expression" or fact.relation != "equation":
        return None
    expression = fact.column_label

    def compact(value):
        return re.sub(
            r"\s+",
            "",
            value.replace(",", ".")
            .replace("×", "*")
            .replace("·", "*")
            .replace("−", "-"),
        )

    if compact(expression + "=" + fact.value_raw) not in compact(fact.evidence_text):
        return None
    value, expected = scalar(fact), arithmetic(expression)
    if value is None or expected is None:
        return None
    # Выражение проверяется в исходных числах; единица результата не меняет арифметику.
    raw = Decimal(fact.value_raw.replace(",", "."))
    precision = (
        len(fact.value_raw.replace(",", ".").partition(".")[2])
        if "." in fact.value_raw.replace(",", ".")
        else 6
    )
    tolerance = Decimal(".5") * Decimal(10) ** -precision + abs(expected) * Decimal(
        ".000001"
    )
    mismatch = abs(raw - expected) > tolerance
    return EngineeringCheck(
        "explicit_arithmetic",
        "confirmed" if mismatch else "no_finding",
        engineering_object_key(fact),
        property_key(fact),
        (item,),
        "arithmetic_result_mismatch" if mismatch else "arithmetic_verified",
        {
            "expression": expression,
            "expected": str(expected),
            "stated": str(raw),
            "unit": fact.unit_raw,
            "tolerance": str(tolerance),
        },
    )


def resolve_reference(item, facts, page_texts, enabled) -> EngineeringCheck:
    """Разрешает ссылку только в определение, не принимая другое упоминание за документ."""
    target = normalize_label(item.fact.value_raw)
    definitions = tuple(
        other
        for other in facts
        if other.fact.relation == "defines_reference"
        and normalize_label(other.fact.identifier) == target
        and (enabled or other.page == item.page)
    )
    if definitions:
        status, reason, missing = "no_finding", "reference_resolved", ()
    else:
        status, reason = (
            "not_evaluable",
            "referenced_material_not_observed"
            if enabled
            else "document_context_disabled",
        )
        missing = (item.fact.value_raw,)
    return EngineeringCheck(
        "reference_resolution",
        status,
        engineering_object_key(item.fact),
        property_key(item.fact),
        (item, *definitions),
        reason,
        {
            "target": item.fact.value_raw,
            "checked_pages": sorted(page_texts) if enabled else [item.page],
        },
        missing,
    )


def _typed(members, kind):
    return tuple(item for item in members if _fact_kind(item.fact) == kind)


def _fact_kind(fact):
    """Известные явные названия не меняют исходную модель факта."""
    if fact.property_type == "design_fire_flow":
        return fact.property_type
    aliases = {
        "гарантированный напор": "available_head",
        "располагаемый напор": "available_head",
        "требуемый напор": "required_head",
        "полезный противопожарный запас": "useful_fire_volume",
        "расчетный пожарный расход": "design_fire_flow",
        "длительность пожаротушения": "fire_duration",
        "гарантированная подпитка": "guaranteed_refill_flow",
    }
    aliases.update(
        {
            "внутренний диаметр": "internal_diameter",
            "наружный диаметр": "outer_diameter",
            "толщина стенки": "wall_thickness",
            "длина трубопровода": "pipe_length",
            "расчетный расход": "design_flow",
            "скорость потока": "flow_velocity",
            "потери напора": "head_loss",
        }
    )
    return aliases.get(normalize_label(fact.property_name), fact.property_type)


def _one(members, kind):
    values = _typed(members, kind)
    # Повтор одного значения на другом листе сохраняется, разные значения не выбираются.
    keys = {(item.fact.value_raw, item.fact.unit_raw) for item in values}
    return values[0] if len(keys) == 1 else None


def _head_balance(members):
    available, required = (
        _one(members, "available_head"),
        _one(members, "required_head"),
    )
    if available is None or required is None:
        return ()
    if not available.fact.scope_operating_mode or not (
        re.search(
            r"располагаем|гарантирован|available", available.fact.evidence_text, re.I
        )
        and re.search(r"требуем|необходим|required", required.fact.evidence_text, re.I)
    ):
        return (
            EngineeringCheck(
                "available_head",
                "not_evaluable",
                engineering_object_key(available.fact),
                "head_balance",
                (available, required),
                "head_conditions_not_established",
                missing_data=("same_operating_mode_and_head_roles",),
            ),
        )
    a, r = scalar(available.fact), scalar(required.fact)
    if not a or not r or a.dimension != r.dimension:
        return _unavailable(
            "available_head",
            (available, required),
            "incompatible_head_units",
            "compatible_head_units",
        )
    tolerance = comparison_tolerance(a, r)
    mismatch = a.value + tolerance < r.value
    return (
        EngineeringCheck(
            "available_head",
            "confirmed" if mismatch else "no_finding",
            engineering_object_key(available.fact),
            "head_balance",
            (available, required),
            "available_below_required" if mismatch else "head_balance_verified",
            {
                "available": str(a.value),
                "required": str(r.value),
                "dimension": a.dimension,
                "tolerance": str(tolerance),
            },
        ),
    )


def _fire_reserve(members):
    volume, flow, duration = (
        _one(members, key)
        for key in ("useful_fire_volume", "design_fire_flow", "fire_duration")
    )
    if not volume:
        return ()
    if not volume.fact.scope_operating_mode or not re.search(
        r"полезн|useful", volume.fact.evidence_text, re.I
    ):
        return (
            EngineeringCheck(
                "fire_reserve",
                "not_evaluable",
                engineering_object_key(volume.fact),
                "fire_reserve",
                (volume,),
                "useful_volume_or_mode_not_established",
                missing_data=("useful_volume_and_operating_mode",),
            ),
        )
    if not flow or not duration:
        return (
            EngineeringCheck(
                "fire_reserve",
                "not_evaluable",
                engineering_object_key(volume.fact),
                "fire_reserve",
                (volume,),
                "fire_balance_inputs_missing",
                missing_data=tuple(
                    name
                    for name, value in (
                        ("design_fire_flow", flow),
                        ("fire_duration", duration),
                    )
                    if not value
                ),
            ),
        )
    v, q, t = scalar(volume.fact), scalar(flow.fact), scalar(duration.fact)
    if (
        not v
        or not q
        or not t
        or (v.dimension, q.dimension, t.dimension)
        != ("volume_m3", "flow_m3h", "time_h")
    ):
        return _unavailable(
            "fire_reserve",
            (volume, flow, duration),
            "incompatible_fire_balance_units",
            "compatible_volume_flow_time_units",
        )
    # Подпитка учитывается только явно; отсутствие записи не означает нулевой приток.
    refill = _one(members, "guaranteed_refill_flow")
    if not refill:
        return (
            EngineeringCheck(
                "fire_reserve",
                "not_evaluable",
                engineering_object_key(volume.fact),
                "fire_reserve",
                (volume, flow, duration),
                "refill_not_established",
                missing_data=("guaranteed_refill_flow_or_explicit_zero",),
            ),
        )
    inflow = scalar(refill.fact)
    if (
        not inflow
        or inflow.dimension != q.dimension
        or min(v.value, q.value, t.value, inflow.value) < 0
    ):
        return ()
    expected = max(Decimal(0), q.value - inflow.value) * t.value
    tolerance = (
        v.tolerance
        + (q.tolerance + inflow.tolerance) * t.value
        + abs(q.value - inflow.value) * t.tolerance
        + expected * Decimal(".000001")
    )
    mismatch = v.value + tolerance < expected
    return (
        EngineeringCheck(
            "fire_reserve",
            "confirmed" if mismatch else "no_finding",
            engineering_object_key(volume.fact),
            "fire_reserve",
            (volume, flow, duration, refill),
            "insufficient_useful_fire_volume" if mismatch else "fire_balance_verified",
            {
                "formula": "max(0,Q-Qin)*t",
                "required_m3": str(expected),
                "accepted_m3": str(v.value),
                "flow_m3h": str(q.value),
                "refill_m3h": str(inflow.value),
                "hours": str(t.value),
                "tolerance": str(tolerance),
            },
        ),
    )


def _procurement(members):
    required, purchased = (
        _typed(members, "design_quantity"),
        _typed(members, "purchase_quantity"),
    )
    if not required or not purchased:
        return ()
    completeness = _typed(members, "inventory_complete")
    sides = {
        item.fact.value_raw
        for item in completeness
        if re.search(r"(?<!не )\bполн\w*|\bcomplete\b", item.fact.evidence_text, re.I)
        and not re.search(
            r"не\s*полн|not\s+complete|incomplete|частичн|partial",
            item.fact.evidence_text,
            re.I,
        )
    }
    observations = (*required, *purchased, *completeness)
    if not {"design", "procurement"} <= sides:
        return (
            EngineeringCheck(
                "procurement_quantity",
                "not_evaluable",
                engineering_object_key(required[0].fact),
                "procurement_quantity",
                observations,
                "incomplete_inventory",
                missing_data=("complete_design_and_procurement_lists",),
            ),
        )
    packs = _typed(members, "pack_size")
    package_size = scalar(packs[0].fact) if len(packs) == 1 else None
    totals = []
    seen = set()
    for side in (required, purchased):
        total = Decimal(0)
        for item in side:
            key = (item.fact.source_item_id or item.fact_id, item.fact.property_type)
            if key in seen:
                continue
            seen.add(key)
            if normalize_label(item.fact.unit_raw) in {"уп", "уп.", "упак", "упак."}:
                if (
                    not package_size
                    or package_size.dimension != "count"
                    or package_size.value <= 0
                ):
                    return _unavailable(
                        "procurement_quantity",
                        observations,
                        "package_size_not_established",
                        "positive_package_size",
                    )
                raw = scalar_type_count(item.fact.value_raw)
                if raw is None:
                    return _unavailable(
                        "procurement_quantity",
                        observations,
                        "package_quantity_not_established",
                        "numeric_package_quantity",
                    )
                total += raw * package_size.value
            else:
                value = scalar(item.fact)
                if not value or value.dimension != "count" or value.value < 0:
                    return _unavailable(
                        "procurement_quantity",
                        observations,
                        "inventory_units_not_established",
                        "compatible_inventory_units",
                    )
                total += value.value
        totals.append(total)
    deficit = max(Decimal(0), totals[0] - totals[1])
    return (
        EngineeringCheck(
            "procurement_quantity",
            "confirmed" if deficit else "no_finding",
            engineering_object_key(required[0].fact),
            "procurement_quantity",
            (*observations, *packs),
            "procurement_deficit" if deficit else "quantity_covered",
            {
                "required": str(totals[0]),
                "purchased": str(totals[1]),
                "deficit": str(deficit),
                "unit": "шт",
            },
        ),
    )


def scalar_type_count(value):
    """Принимает только неотрицательное числовое количество без диапазонов."""
    return (
        Decimal(value.replace(",", "."))
        if re.fullmatch(r"\d+(?:[.,]\d+)?", value)
        else None
    )


def _table_sums(members):
    totals = tuple(
        item for item in members if item.fact.relation in {"sum_total", "table_total"}
    )
    if not totals:
        return ()
    closure = tuple(item for item in members if item.fact.relation == "table_end")
    parts = tuple(item for item in members if item.fact.relation == "sum_component")
    results = []
    for total in totals:
        selected = tuple(
            item for item in parts if item.fact.column_label == total.fact.column_label
        )
        observations = (*selected, total, *closure)
        statistics = defaultdict(set)
        for item in selected:
            text = normalize_label(
                item.fact.row_label
                + " "
                + item.fact.property_name
                + " "
                + item.fact.scope_condition
            )
            consumer = engineering_object_key(item.fact)
            if item.fact.column_binding == "pdf_geometry":
                base = normalize_label(
                    re.sub(
                        r"средн\w*|максим\w*|[()]", "", item.fact.row_label, flags=re.I
                    )
                )
                if len(base) > 3:
                    consumer = "row:" + base
            for pattern, role in (
                (r"средн|average|mean", "average"),
                (r"максим|maximum|peak", "maximum"),
            ):
                if re.search(pattern, text):
                    statistics[consumer].add(role)
        if any(roles == {"average", "maximum"} for roles in statistics.values()):
            results.extend(
                _unavailable(
                    "table_sum",
                    observations,
                    "alternative_statistics_not_additive",
                    "independent_consumers_or_explicit_mode_selection",
                )
            )
            continue
        rule_quote = normalize_label(total.fact.calculation_evidence)
        if (
            total.fact.calculation_rule != "sum"
            or not rule_quote
            or rule_quote not in normalize_label(total.fact.evidence_text)
            or not re.search(r"сумм\w*\s+(?:всех\s+)?строк|sum\s+of\s+rows", rule_quote)
        ):
            results.extend(
                _unavailable(
                    "table_sum",
                    observations,
                    "total_rule_not_established",
                    "explicit_additive_rule",
                )
            )
            continue
        modes = {
            normalize_label(item.fact.scope_operating_mode)
            for item in (*selected, total)
        }
        if len(modes) != 1 or "" in modes:
            results.extend(
                _unavailable(
                    "table_sum",
                    observations,
                    "incomparable_or_unknown_operating_modes",
                    "compatible_operating_modes",
                )
            )
            continue
        if any(item.fact.column_binding == "ambiguous" for item in (*selected, total)):
            results.extend(
                _unavailable(
                    "table_sum",
                    observations,
                    "column_binding_ambiguous",
                    "verified_column_binding",
                )
            )
            continue
        if not total.fact.calculation_group and any(
            engineering_object_key(item.fact) != engineering_object_key(total.fact)
            for item in selected
        ):
            results.extend(
                _unavailable(
                    "table_sum",
                    observations,
                    "calculation_group_not_established",
                    "same_object_or_explicit_calculation_group",
                )
            )
            continue
        row_ids = {
            row for item in (*selected, total) for row in item.fact.source_row_ids
        }
        manifest = {row for item in closure for row in item.fact.source_row_ids}
        if not closure or not selected or not row_ids or manifest != row_ids:
            results.append(
                EngineeringCheck(
                    "table_sum",
                    "not_evaluable",
                    engineering_object_key(total.fact),
                    property_key(total.fact),
                    observations,
                    "table_extent_not_established",
                    missing_data=("complete_table_rows_and_end",),
                )
            )
            continue
        values, stated = [scalar(item.fact) for item in selected], scalar(total.fact)
        if not stated or any(
            value is None or value.dimension != stated.dimension for value in values
        ):
            results.extend(
                _unavailable(
                    "table_sum",
                    observations,
                    "table_units_not_established",
                    "compatible_table_units",
                )
            )
            continue
        seen_rows = set()
        unique = []
        for item, value in zip(selected, values, strict=True):
            row = item.fact.source_row_ids or (item.fact.row_label,)
            if not any(row) or row in seen_rows:
                continue
            seen_rows.add(row)
            unique.append(value)
        if len(unique) != len(selected):
            results.append(
                EngineeringCheck(
                    "table_sum",
                    "not_evaluable",
                    engineering_object_key(total.fact),
                    property_key(total.fact),
                    observations,
                    "ambiguous_repeated_rows",
                    missing_data=("unique_table_row_identity",),
                )
            )
            continue
        expected = sum((value.value for value in unique), Decimal(0))
        tolerance = comparison_tolerance(stated, *unique)
        mismatch = abs(expected - stated.value) > tolerance
        results.append(
            EngineeringCheck(
                "table_sum",
                "confirmed" if mismatch else "no_finding",
                engineering_object_key(total.fact),
                property_key(total.fact),
                observations,
                "table_total_mismatch" if mismatch else "table_total_verified",
                {
                    "expected": str(expected),
                    "stated": str(stated.value),
                    "dimension": stated.dimension,
                    "tolerance": str(tolerance),
                },
            )
        )
    return tuple(results)


def _unavailable(rule, observations, reason, missing):
    """Сохраняет конкретную причину невыполненной проверки вместо молчаливого пропуска."""
    first = observations[0]
    return (
        EngineeringCheck(
            rule,
            "not_evaluable",
            engineering_object_key(first.fact),
            property_key(first.fact),
            tuple(observations),
            reason,
            missing_data=(missing,),
        ),
    )


def literal_fact_value(item: LocatedDocumentFact) -> bool:
    """Тип/отношение модели не подменяют числовое значение и роли исходной записи."""
    fact = item.fact
    if property_key(fact).endswith(":system_purpose"):
        value = normalized_value(fact)
        return value is not None and value == normalized_value(
            replace(fact, value_raw=fact.evidence_text)
        )
    if fact.property_type in {
        "document_reference",
        "document_definition",
        "topic_reference",
    }:
        parsed = explicit_project_dependencies(fact.evidence_text, item.page)
        if any(
            other.identifier == fact.identifier
            and other.value_raw == fact.value_raw
            and other.relation == fact.relation
            for other in parsed
        ):
            return True
    if fact.property_type == "inventory_complete":
        pattern = r"проект|design" if fact.value_raw == "design" else r"закуп|procure"
        return bool(re.search(pattern, fact.evidence_text, re.I))
    if fact.relation == "table_end":
        return bool(re.search(r"конец|окончание|end", fact.evidence_text, re.I))
    value = normalize_label(fact.value_raw).replace(",", ".")
    quote = normalize_label(fact.evidence_text).replace(",", ".")
    return bool(
        value
        and re.search(
            r"(?<![\w.])(?:[ø⌀]|dn\s*|ду\s*)?" + re.escape(value) + r"(?!\w|\.\d)",
            quote,
        )
    )


def program_evidence_eligible(evidence) -> bool:
    """Использует и VLM-факты, если объект, значение и условия проверяются в исходной цитате."""
    item = evidence.observation
    if evidence.grounding != "literal_text" or not literal_fact_value(item):
        return False
    fact = item.fact
    if fact.extraction_method in {"pdf_text", "pdf_table"}:
        return True
    quote = normalize_label(fact.evidence_text)
    identifier = normalize_label(fact.identifier)
    if not identifier or not re.search(
        r"(?<!\w)" + re.escape(identifier) + r"(?!\w)", quote
    ):
        return False
    role_patterns = {
        "darcy_friction_factor": r"дарси|darcy",
        "available_head": r"гарантирован|располагаем|available",
        "required_head": r"требуем|необходим|required",
        "useful_fire_volume": r"пожар|fire",
        "design_fire_flow": r"пожар|fire",
        "fire_duration": r"пожар|fire",
        "guaranteed_refill_flow": r"гарантирован|guaranteed",
        "design_quantity": r"проект|design|требуется",
        "purchase_quantity": r"закуп|purchase|procure",
        "pack_size": r"упак|pack",
    }
    role = role_patterns.get(_fact_kind(fact))
    if role and not re.search(role, quote):
        return False
    aliases = {
        "fire": "пожар",
        "normal": "штатн",
        "emergency": "авари",
        "winter_design": "зимн",
    }
    for value in (
        fact.scope_location,
        fact.scope_segment,
        fact.scope_operating_mode,
        fact.scope_condition,
    ):
        value = normalize_label(value)
        if value and value not in quote and aliases.get(value, value) not in quote:
            return False
    return True


def _hydraulics(members):
    """Проверяет круглое сечение и формулу Дарси только для явно установленного участка."""
    velocity, loss = _one(members, "flow_velocity"), _one(members, "head_loss")
    if not velocity and not loss:
        return ()
    accepted = velocity or loss
    if (
        accepted.fact.subject_type not in {"pipe", "pipeline", "pipeline_segment"}
        or not accepted.fact.scope_operating_mode
        or not (
            accepted.fact.scope_segment
            or accepted.fact.subject_type == "pipeline_segment"
        )
    ):
        return _unavailable(
            "hydraulic_dependency",
            (accepted,),
            "pipe_segment_or_mode_not_established",
            "explicit_pipe_segment_and_operating_mode",
        )
    kinds = {
        "flow_velocity",
        "head_loss",
        "internal_diameter",
        "outer_diameter",
        "wall_thickness",
        "nominal_diameter",
        "design_flow",
        "pipe_length",
        "darcy_friction_factor",
        "local_resistance_sum",
    }
    observations = tuple(item for item in members if _fact_kind(item.fact) in kinds)
    values = {
        kind: scalar(item.fact)
        for kind in kinds
        if (item := _one(members, kind)) is not None
    }
    diameter = _pipe_diameter(members, values)
    results = []
    if velocity:
        flow, stated = values.get("design_flow"), values.get("flow_velocity")
        if (
            diameter
            and flow
            and stated
            and flow.dimension == "flow_m3h"
            and stated.dimension == "velocity_ms"
            and flow.value >= 0
        ):
            d = diameter.value / 1000
            area = Decimal("3.14159265358979323846") * d * d / 4
            expected = flow.value / 3600 / area
            tolerance = (
                stated.tolerance
                + flow.tolerance / 3600 / area
                + abs(expected)
                * (2 * diameter.tolerance / diameter.value + Decimal(".001"))
            )
            mismatch = abs(expected - stated.value) > tolerance
            results.append(
                EngineeringCheck(
                    "velocity_balance",
                    "confirmed" if mismatch else "no_finding",
                    engineering_object_key(velocity.fact),
                    "flow_velocity",
                    (velocity, *(item for item in observations if item != velocity)),
                    "velocity_balance_mismatch"
                    if mismatch
                    else "velocity_balance_verified",
                    {
                        "formula": "4Q/(pi*d_internal^2)",
                        "expected": str(expected),
                        "stated": str(stated.value),
                        "unit": "м/с",
                        "tolerance": str(tolerance),
                    },
                )
            )
        else:
            missing = ()
            if not flow or flow.dimension != "flow_m3h" or flow.value < 0:
                missing += ("расчётный расход в совместимых единицах",)
            if not diameter:
                missing += ("внутренний диаметр, без подстановки DN",)
            if not stated or stated.dimension != "velocity_ms":
                missing += ("скорость в м/с",)
            results.extend(_hydraulic_question(velocity, observations, missing))
    if loss:
        factor, length, speed, local, stated = (
            values.get(kind)
            for kind in (
                "darcy_friction_factor",
                "pipe_length",
                "flow_velocity",
                "local_resistance_sum",
                "head_loss",
            )
        )
        factor_fact = _one(members, "darcy_friction_factor")
        explicit_darcy = factor_fact and re.search(
            r"дарси|darcy", factor_fact.fact.evidence_text, re.I
        )
        total_loss = re.search(r"суммарн|полн|total", loss.fact.evidence_text, re.I)
        if (
            diameter
            and all((factor, length, speed, local, stated))
            and explicit_darcy
            and total_loss
            and factor.dimension == local.dimension == "number"
            and length.dimension == stated.dimension == "length_mm"
            and speed.dimension == "velocity_ms"
            and min(factor.value, length.value, speed.value, local.value) >= 0
        ):
            d, distance = diameter.value / 1000, length.value / 1000
            coefficient = factor.value * distance / d + local.value
            expected = coefficient * speed.value * speed.value / Decimal("19.6133")
            tolerance = stated.tolerance / 1000 + abs(expected) * Decimal(".001")
            tolerance += (
                (
                    factor.tolerance * distance / d
                    + factor.value * length.tolerance / 1000 / d
                    + factor.value * distance / (d * d) * diameter.tolerance / 1000
                    + local.tolerance
                )
                * speed.value**2
                / Decimal("19.6133")
            )
            tolerance += (
                coefficient * abs(speed.value) * speed.tolerance / Decimal("9.80665")
            )
            mismatch = abs(expected - stated.value / 1000) > tolerance
            results.append(
                EngineeringCheck(
                    "head_loss_balance",
                    "confirmed" if mismatch else "no_finding",
                    engineering_object_key(loss.fact),
                    "head_loss",
                    (loss, *(item for item in observations if item != loss)),
                    "head_loss_mismatch" if mismatch else "head_loss_verified",
                    {
                        "formula": "(lambda_Darcy*L/d_internal+sum_zeta)*v^2/(2g)",
                        "expected": str(expected),
                        "stated": str(stated.value / 1000),
                        "unit": "м",
                        "tolerance": str(tolerance),
                    },
                )
            )
        else:
            missing = tuple(
                label
                for kind, label in (
                    ("darcy_friction_factor", "коэффициент Дарси"),
                    ("pipe_length", "длина участка"),
                    ("flow_velocity", "скорость"),
                    ("local_resistance_sum", "местные сопротивления"),
                )
                if not values.get(kind)
            )
            if not diameter:
                missing += ("внутренний диаметр",)
            if not explicit_darcy:
                missing += ("коэффициент именно Дарси, не Фаннинга",)
            if not total_loss:
                missing += ("суммарные, а не только линейные потери",)
            results.extend(
                _hydraulic_question(
                    loss, observations, missing or ("совместимые единицы и условия",)
                )
            )
    return tuple(results)


def _pipe_diameter(members, values):
    """Берёт внутренний диаметр либо выводит его из явно указанных наружного и стенки."""
    diameter = values.get("internal_diameter")
    if diameter is None:
        outer_fact = _one(members, "outer_diameter")
        # Общий нормализатор намеренно хранит наружный диаметр отдельно от длины.
        # Здесь роль уже проверена, а исходное числовое значение и единица сохранены.
        outer = (
            scalar(replace(outer_fact.fact, property_name="длина"))
            if outer_fact
            else None
        )
        wall = values.get("wall_thickness")
        if (
            outer
            and wall
            and outer.dimension == wall.dimension == "length_mm"
            and outer.value > 2 * wall.value
        ):
            diameter = Scalar(
                "length_mm",
                outer.value - 2 * wall.value,
                outer.tolerance + 2 * wall.tolerance,
            )
    if diameter is None or diameter.dimension != "length_mm" or diameter.value <= 0:
        return None
    return diameter


def _hydraulic_question(accepted, observations, missing):
    """Вопрос содержит принятое значение и известные исходные данные вместо общего «проверить расчёт»."""
    known = [
        item.fact.evidence_text
        for item in observations
        if item != accepted and scalar(item.fact) is not None
    ]
    status = "needs_review" if known else "not_evaluable"
    return (
        EngineeringCheck(
            "hydraulic_dependency",
            status,
            engineering_object_key(accepted.fact),
            property_key(accepted.fact),
            (accepted, *(item for item in observations if item != accepted)),
            "hydraulic_inputs_not_established",
            {
                "accepted": accepted.fact.value_raw + " " + accepted.fact.unit_raw,
                "inputs": known,
                "missing": list(missing),
            },
            missing_data=missing,
        ),
    )
