# services/analysis-service/src/pdrd_analysis_service/domain/requirement_plan.py

"""Независимые проверки от буквальных N/T/U-требований к уже извлечённым фактам.

Поддерживаются однозначные числовые границы и явно требуемое обоснование расчётом.
Неподдержанная формула, неизвестная редакция и неполная область применения остаются
в плане; совпадение векторного поиска не становится доказательством применимости.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

from pdrd_analysis_service.domain.design_decisions import stable_id
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    normalize_label,
    property_key,
    scope_key,
)
from pdrd_analysis_service.domain.engineering_assertion import engineering_object_key
from pdrd_analysis_service.domain.engineering_calculations import (
    comparison_tolerance,
    scalar,
)
from pdrd_analysis_service.domain.engineering_checks import EngineeringCheck
from pdrd_analysis_service.domain.evidence_audit import (
    bind_evidence,
    literal_contains,
)

_CODE = re.compile(r"\b(?:СП|ГОСТ(?:\s+Р)?|СНиП)\s+\d+(?:[.\-]\d+)*", re.I)
_PROPERTY = (
    r"(?:расч[её]тный\s+)?(?:расход(?:\s+воды)?|напор|давление|внутренний\s+диаметр|"
    r"наружный\s+диаметр|номинальный\s+диаметр|диаметр(?:а|ов|ы)?|скорость|"
    r"полезный\s+пожарный\s+запас|объ[её]м|количество|температура)"
)
_BOUND = re.compile(
    r"(?P<property>"
    + _PROPERTY
    + r")\s*(?:(?:должен|должна|должно)\s+(?:быть|составлять)\s+)?"
    r"(?P<operator>не менее|не более|>=|<=|≥|≤)\s*(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>м[³3]/сут|м[³3]/ч|л/с|м/с|м[³3]|кПа|МПа|мм|шт\.?|°С|м)(?!\w)",
    re.I,
)
_CLASSES = (
    (r"гидрант", r"гидрант"),
    (r"резервуар", r"резервуар"),
    (r"трубопровод|водопровод|трубы", r"трубопровод|водопровод|труб"),
    (r"наружн\w*\s+пожаротушени", r"наружн\w*.*пожаротушени"),
    (r"внутренн\w*\s+пожаротушени", r"внутренн\w*.*пожаротушени"),
    (r"котельн", r"котельн"),
)


@dataclass(frozen=True, slots=True)
class RequirementRecord:
    """Положение источника и судьба его проверки, в том числе успешной или невыполнимой."""

    requirement_id: str
    source_kind: str
    source_ids: tuple[str, ...]
    source_point_id: str
    document: str
    edition: str
    clause: str
    quote: str
    obligation: str
    currency: str
    currency_evidence: tuple[dict, ...]
    conditions: str
    object_key: str
    property_key: str
    rule: str
    required_data: tuple[str, ...]
    applicability: str
    result: str
    reason_code: str
    observations: tuple[LocatedDocumentFact, ...] = ()
    comparison: dict | None = None

    def as_dict(self) -> dict:
        """Сохраняет буквальную цитату, исходные IDs и ограничения независимо от findings."""
        return asdict(self)


def _clauses(text: str) -> tuple[tuple[str, str], ...]:
    """Разделяет только явные пункты; не назначает выдуманный номер ненумерованной строке."""
    matches = list(re.finditer(r"(?m)^\s*(\d+(?:\.\d+)+)\.?\s+", text))
    if not matches:
        return (("", text.strip()),) if text.strip() else ()
    return tuple(
        (
            match[1],
            text[
                match.end() : matches[i + 1].start()
                if i + 1 < len(matches)
                else len(text)
            ].strip(),
        )
        for i, match in enumerate(matches)
    )


def _currency(source, sources, assessed_on: str) -> tuple[str, tuple[dict, ...]]:
    """Требует датированное подтверждение именно этой редакции в переданных N-фрагментах корпуса.

    Пример справочной записи: «Статус СП 100.13330.2025 на 2026-10-19: действует;
    требования обязательны». Само наличие года в имени файла ничего не подтверждает.
    """
    code = _document_code(source)
    if not code:
        return "unknown", ()
    evidence = []
    status = "unknown"
    for other in sources:
        if (
            other.kind != "N"
            or not other.source_id.startswith("N")
            or not other.point_id
            or not str(other.page).isdigit()
            or int(other.page) < 1
        ):
            continue
        for line in other.text.splitlines():
            if not literal_contains(line, code):
                continue
            stamp = re.search(
                r"статус\s+"
                + re.escape(code)
                + r"\s+на\s+(\d{4}-\d{2}-\d{2})\s*:\s*(действует|отмен[её]н)",
                line,
                re.I,
            )
            if stamp is None or stamp[1] != assessed_on:
                continue
            statement = re.split(
                r"\bстатус\s+", line[stamp.end() :], maxsplit=1, flags=re.I
            )[0]
            state = (
                "cancelled"
                if stamp[2].casefold().startswith("отмен")
                else "current_mandatory"
                if re.search(
                    r"(?:^|[;,.]\s*)требования\s+обязательны(?:[;,.]|$)",
                    statement,
                    re.I,
                )
                else "current_binding_unknown"
            )
            evidence.append(
                {
                    "source_id": other.source_id,
                    "quote": line,
                    "page": other.page,
                    "as_of": stamp[1],
                }
            )
            status = "conflicting_status" if status not in {"unknown", state} else state
    return status, tuple(evidence)


def _document_code(source) -> str:
    """Не принимает ссылку внутри пункта за обозначение самого документа."""
    match = _CODE.search(source.source_file)
    if match is None:
        match = _CODE.match(source.text.strip())
    return match[0] if match else ""


def _rule_context(quote: str, bound) -> str:
    """Не переносит ограничение между предложениями и разными объектами одного фрагмента."""
    if bound is None:
        return quote
    separators = tuple(re.finditer(r"[;\n!?]|\.(?=\s+[А-ЯA-Z])", quote))
    start = max((m.end() for m in separators if m.end() <= bound.start()), default=0)
    end = min(
        (m.start() for m in separators if m.start() >= bound.end()), default=len(quote)
    )
    return quote[start:end].strip()


def _property_family(name: str) -> str:
    """Отделяет определения диаметра и группирует только явные названия величин."""
    name = normalize_label(name)
    for pattern, key in (
        (r"внутренн.*диаметр", "internal_diameter"),
        (r"наружн.*диаметр", "outer_diameter"),
        (r"номинальн.*диаметр|условн.*диаметр", "nominal_diameter"),
        (r"диаметр", "diameter"),
        (r"расход", "flow"),
        (r"напор", "head"),
        (r"давлен", "pressure"),
        (r"скорост", "velocity"),
        (r"объем|запас", "volume"),
        (r"количеств", "quantity"),
        (r"температур", "temperature"),
    ):
        if re.search(pattern, name):
            return key
    return name


def _subject_matches(quote: str, item: LocatedDocumentFact) -> bool:
    """Класс объекта подтверждается исходной цитатой проекта, а не только ярлыком модели."""
    if literal_contains(quote, item.fact.identifier):
        return True
    return any(
        re.search(source_pattern, quote, re.I)
        and re.search(project_pattern, item.fact.evidence_text, re.I)
        for source_pattern, project_pattern in _CLASSES
    )


def _condition_state(condition: str, item: LocatedDocumentFact) -> str:
    """Проверяет явный режим; остальные условия не угадывает по близости текста."""
    if not condition:
        return "applicable"
    modes = ((r"пожарн", "fire"), (r"аварийн", "emergency"), (r"штатн|рабоч", "normal"))

    def mode(text: str) -> str:
        """Узнаёт только однозначно подписанный эксплуатационный режим."""
        found = {key for pattern, key in modes if re.search(pattern, text, re.I)}
        return next(iter(found)) if len(found) == 1 else ""

    expected = mode(condition)
    actual_text = item.fact.scope_operating_mode + " " + item.fact.scope_condition
    actual = mode(actual_text) or (
        actual_text.strip()
        if actual_text.strip() in {"fire", "normal", "emergency"}
        else ""
    )
    # Совпадение режима не подтверждает температуру, тип здания и другие предпосылки.
    simple_mode = bool(
        re.fullmatch(
            r"(?:пожарном|аварийном|штатном|рабочем)\s+режиме", condition, re.I
        )
    )
    if simple_mode and expected and actual:
        return "applicable" if expected == actual else "not_applicable"
    return "applicable" if literal_contains(actual_text, condition) else "unknown"


def _subject_scope_known(context: str, item: LocatedDocumentFact) -> bool:
    """Ограниченный набор общих классов не поглощает дополнительные квалификаторы нормы."""
    match = re.search(_PROPERTY, context, re.I)
    if match is None:
        return False
    prefix = context[: match.start()]
    prefix = re.sub(r"\b(?:при|если)\b[^,;]+[,;]?", "", prefix, flags=re.I)
    prefix = re.sub(r"^\s*для\s+", "", prefix, flags=re.I).strip(" ,:")
    generic = (
        r"(?:пожарн\w*\s+)?гидрант\w*|резервуар\w*|трубопровод\w*|водопровод\w*|"
        r"(?:наружн\w*|внутренн\w*)\s+пожаротушени\w*|котельн\w*"
    )
    return bool(re.fullmatch(generic, prefix, re.I)) or normalize_label(
        prefix
    ) == normalize_label(item.fact.identifier)


def build_requirement_plan(
    facts: tuple[LocatedDocumentFact, ...],
    *,
    page_texts: dict[int, str],
    sources: tuple,
    assessed_on: str,
) -> tuple[RequirementRecord, ...]:
    """Формирует проверки каждого переданного положения независимо от списка модельных замечаний."""
    records = []
    for source in sources:
        currency, currency_evidence = (
            _currency(source, sources, assessed_on)
            if source.kind == "N"
            else ("not_normative", ())
        )
        document = _document_code(source) or source.source_file
        edition_match = re.search(r"(?:[.-])(\d{4})\b", document)
        for clause, quote in _clauses(source.text) or (("", ""),):
            obligation = (
                "recommendation"
                if re.search(r"рекомендуется|рекомендован", quote, re.I)
                else "normative_requirement"
                if source.kind == "N"
                else "technical_assignment_requirement"
                if source.kind == "T"
                else "user_requirement"
            )
            bounds = list(_BOUND.finditer(quote))
            calculation_required = bool(
                re.search(
                    r"(?:долж\w*|следует|необходимо|определяют|определять|определяться)"
                    r"[^.!?]{0,200}расч[её]т\w*",
                    quote,
                    re.I,
                )
            )
            matches = [*bounds, *((None,) if calculation_required else ())] or [None]
            for number, bound in enumerate(matches):
                calculation = bound is None and calculation_required
                context = _rule_context(quote, bound)
                calculated_property = re.search(_PROPERTY, context, re.I)
                prop = (
                    bound["property"]
                    if bound
                    else calculated_property[0]
                    if calculation and calculated_property
                    else ""
                )
                condition_match = re.search(
                    r"(?:\bпри\b|\bесли\b)\s+([^,;:.]+)", context, re.I
                )
                condition = condition_match[1].strip() if condition_match else ""
                # Если условие стоит перед величиной без запятой, отделяем только его текст.
                if (
                    bound
                    and condition_match
                    and condition_match.start() < context.find(bound[0])
                ):
                    condition = re.split(_PROPERTY, condition, maxsplit=1, flags=re.I)[
                        0
                    ].strip()
                related = tuple(
                    item
                    for item in facts
                    if _subject_matches(context, item)
                    and (
                        _property_family(item.fact.property_name)
                        == _property_family(prop)
                    )
                )
                groups = {}
                for observed in related:
                    key = (
                        engineering_object_key(observed.fact),
                        "" if calculation else property_key(observed.fact),
                        scope_key(observed.fact),
                    )
                    groups.setdefault(key, []).append(observed)
                targets = tuple(tuple(items) for items in groups.values()) or ((),)
                for observations in targets:
                    item = observations[0] if observations else None
                    ids = (
                        source.source_id,
                        *(part["source_id"] for part in currency_evidence),
                    )
                    record = RequirementRecord(
                        stable_id(
                            "req-",
                            (
                                source.kind,
                                source.point_id or source.source_id,
                                clause,
                                quote,
                                number,
                                engineering_object_key(item.fact) if item else "",
                                property_key(item.fact)
                                if item and not calculation
                                else "",
                                scope_key(item.fact) if item else (),
                            ),
                        ),
                        source.kind,
                        tuple(dict.fromkeys(ids)),
                        source.point_id,
                        document,
                        edition_match[1] if edition_match else "",
                        clause,
                        quote,
                        obligation,
                        currency,
                        currency_evidence,
                        condition,
                        engineering_object_key(item.fact) if item else "",
                        property_key(item.fact) if item else prop,
                        "numeric_bound"
                        if bound
                        else "required_calculation"
                        if calculation
                        else "unsupported",
                        (
                            prop,
                            "scope",
                            "source_applicability",
                            *(
                                ("calculation_reference", "calculation_inputs")
                                if calculation
                                else ()
                            ),
                        ),
                        "unknown",
                        "not_evaluable",
                        "unsupported_requirement_rule",
                        observations,
                    )
                    if (
                        not source.source_id.startswith(source.kind)
                        or source.kind not in {"N", "T", "U"}
                        or not source.point_id
                        or not str(source.page).isdigit()
                        or int(source.page) < 1
                        or not quote
                    ):
                        records.append(
                            replace(record, reason_code="incomplete_requirement_source")
                        )
                        continue
                    if bound is None and not calculation:
                        records.append(record)
                        continue
                    if obligation == "recommendation":
                        records.append(
                            replace(
                                record,
                                result="recommendation",
                                reason_code="not_a_mandatory_requirement",
                            )
                        )
                        continue
                    if re.search(
                        r"\bне\s+(?:долж|требу)|допускается|может|вправе", context, re.I
                    ):
                        records.append(
                            replace(
                                record, reason_code="unsupported_requirement_modality"
                            )
                        )
                        continue
                    if sum(
                        other.source_id == source.source_id and other != source
                        for other in sources
                    ):
                        records.append(
                            replace(record, reason_code="ambiguous_source_identifier")
                        )
                        continue
                    if item is None:
                        records.append(
                            replace(
                                record,
                                result="insufficient_data",
                                reason_code="project_object_or_property_not_observed",
                            )
                        )
                        continue
                    if any(
                        bind_evidence(observed, page_texts).status != "supported"
                        for observed in observations
                    ):
                        records.append(
                            replace(
                                record,
                                result="insufficient_data",
                                reason_code="project_evidence_not_bound",
                            )
                        )
                        continue
                    state = _condition_state(condition, item)
                    if not _subject_scope_known(context, item):
                        state = "unknown"
                    if re.search(
                        r"требуем|гарантирован|допустим|максимальн|минимальн",
                        item.fact.property_name,
                        re.I,
                    ):
                        state = "unknown"
                    if re.search(r"кроме|за исключением", context, re.I):
                        state = "unknown"
                    if state != "applicable":
                        records.append(
                            replace(
                                record,
                                applicability=state,
                                result="not_applicable"
                                if state == "not_applicable"
                                else "applicability_unknown",
                                reason_code="requirement_condition_false"
                                if state == "not_applicable"
                                else "requirement_conditions_not_established",
                            )
                        )
                        continue
                    if source.kind == "N" and (
                        currency != "current_mandatory" or not clause
                    ):
                        records.append(
                            replace(
                                record,
                                applicability="unknown",
                                result="not_applicable"
                                if currency == "cancelled"
                                else "applicability_unknown",
                                reason_code="normative_edition_or_binding_unverified",
                            )
                        )
                        continue
                    if obligation == "recommendation":
                        records.append(
                            replace(
                                record,
                                applicability="applicable",
                                result="recommendation",
                                reason_code="not_a_mandatory_requirement",
                            )
                        )
                        continue
                    if calculation:
                        if not any(
                            scalar(observed.fact) is not None
                            for observed in observations
                        ):
                            records.append(
                                replace(
                                    record,
                                    result="insufficient_data",
                                    reason_code="adopted_design_parameter_not_observed",
                                )
                            )
                            continue
                        references = tuple(
                            other
                            for other in facts
                            if engineering_object_key(other.fact)
                            == engineering_object_key(item.fact)
                            and scope_key(other.fact) == scope_key(item.fact)
                            and other.fact.property_type == "calculation_reference"
                            and bind_evidence(other, page_texts).status == "supported"
                        )
                        records.append(
                            replace(
                                record,
                                applicability="applicable",
                                result="insufficient_data"
                                if references
                                else "question",
                                reason_code="calculation_reference_requires_resolution"
                                if references
                                else "required_calculation_not_linked_in_checked_pages",
                                comparison={
                                    "checked_pages": sorted(page_texts),
                                    "assessed_on": assessed_on,
                                },
                            )
                        )
                        continue
                    project = scalar(item.fact)
                    if (
                        len(
                            {
                                (observed.fact.value_raw, observed.fact.unit_raw)
                                for observed in observations
                            }
                        )
                        > 1
                    ):
                        records.append(
                            replace(
                                record,
                                result="insufficient_data",
                                reason_code="conflicting_project_values_require_resolution",
                            )
                        )
                        continue
                    limit = scalar(
                        replace(
                            item.fact, value_raw=bound["value"], unit_raw=bound["unit"]
                        )
                    )
                    if not project or not limit or project.dimension != limit.dimension:
                        records.append(
                            replace(
                                record,
                                applicability="applicable",
                                result="insufficient_data",
                                reason_code="incompatible_or_unknown_parameter_definition",
                            )
                        )
                        continue
                    tolerance = comparison_tolerance(project, limit)
                    minimum = bound["operator"].casefold() in {"не менее", ">=", "≥"}
                    mismatch = (
                        project.value + tolerance < limit.value
                        if minimum
                        else project.value - tolerance > limit.value
                    )
                    comparison = {
                        "project": str(project.value),
                        "limit": str(limit.value),
                        "operator": bound["operator"],
                        "dimension": project.dimension,
                        "source_kind": source.kind,
                        "source_quote": quote,
                        "source_page": source.page,
                        "tolerance": str(tolerance),
                        "assessed_on": assessed_on,
                    }
                    records.append(
                        replace(
                            record,
                            applicability="applicable",
                            result="violated" if mismatch else "satisfied",
                            reason_code="applicable_requirement_violated"
                            if mismatch
                            else "applicable_requirement_satisfied",
                            comparison=comparison,
                        )
                    )
    return _mark_conflicts(tuple(records))


def _conflicting_pairs(records):
    """Сопоставляет границы только одного объекта, свойства, режима и размерности."""
    minimum = {"не менее", ">=", "≥"}
    for normative in records:
        if (
            normative.source_kind != "N"
            or normative.applicability != "applicable"
            or not normative.comparison
            or normative.rule != "numeric_bound"
        ):
            continue
        for customer in records:
            if (
                customer.source_kind not in {"T", "U"}
                or customer.applicability != "applicable"
                or not customer.comparison
                or customer.rule != "numeric_bound"
            ):
                continue
            if (normative.object_key, normative.property_key) != (
                customer.object_key,
                customer.property_key,
            ) or scope_key(normative.observations[0].fact) != scope_key(
                customer.observations[0].fact
            ):
                continue
            n, c = normative.comparison, customer.comparison
            if n["dimension"] != c["dimension"] or (n["operator"] in minimum) == (
                c["operator"] in minimum
            ):
                continue
            low, high = (n, c) if n["operator"] in minimum else (c, n)
            if Decimal(low["limit"]) > Decimal(high["limit"]):
                yield normative, customer


def _mark_conflicts(records):
    """Учитывает конфликт в охвате, сохраняя исходное числовое сравнение и каждое основание."""
    ids = {customer.requirement_id for _, customer in _conflicting_pairs(records)}
    return tuple(
        replace(
            r,
            result="source_conflict",
            reason_code="customer_requirement_conflicts_with_norm",
        )
        if r.requirement_id in ids
        else r
        for r in records
    )


def requirement_checks(
    records: tuple[RequirementRecord, ...],
) -> tuple[EngineeringCheck, ...]:
    """Передаёт выводы в существующую программу доказательств, не выдавая пробелы за нарушения."""
    result = []
    conflicts = {}
    for normative, customer in _conflicting_pairs(records):
        n = normative.comparison
        conflicts.setdefault(customer.requirement_id, []).append(
            EngineeringCheck(
                "requirement_plan_conflict",
                "needs_review",
                customer.object_key,
                customer.property_key,
                customer.observations,
                "customer_requirement_conflicts_with_norm",
                {
                    "normative": normative.quote,
                    "customer": customer.quote,
                    "assessed_on": n["assessed_on"],
                    "requirement_id": customer.requirement_id,
                    "normative_requirement_id": normative.requirement_id,
                },
                source_ids=tuple(
                    dict.fromkeys((*normative.source_ids, *customer.source_ids))
                ),
            )
        )
    for record in records:
        if not record.observations:
            continue
        if record.requirement_id in conflicts:
            result.extend(conflicts[record.requirement_id])
            continue
        status = {
            "violated": "confirmed",
            "satisfied": "no_finding",
            "question": "needs_review",
        }.get(record.result, "not_evaluable")
        result.append(
            EngineeringCheck(
                "requirement_plan",
                status,
                record.object_key,
                record.property_key,
                record.observations,
                record.reason_code,
                {
                    **(record.comparison or {}),
                    "requirement_id": record.requirement_id,
                    "clause": record.clause,
                    "document": record.document,
                    "quote": record.quote,
                    "rule": record.rule,
                    "currency": record.currency,
                    "conditions": record.conditions,
                    "assessed_on": (record.comparison or {}).get("assessed_on", ""),
                },
                missing_data=record.required_data if status == "not_evaluable" else (),
                source_ids=record.source_ids,
            )
        )
    return tuple(result)


def requirement_coverage(records: tuple[RequirementRecord, ...]) -> dict:
    """Считает охват только предоставленных положений, не всего неизвестного нормативного корпуса."""
    applicable = tuple(
        r
        for r in records
        if r.applicability == "applicable" and r.obligation != "recommendation"
    )
    completed = tuple(r for r in applicable if r.result in {"violated", "satisfied"})
    return {
        "scope": "retrieved_requirements_only",
        "requirements": len(records),
        "applicable": len(applicable),
        "completed": len(completed),
        "coverage": len(completed) / len(applicable) if applicable else None,
        "outcomes": {
            state: sum(r.result == state for r in records)
            for state in sorted({r.result for r in records})
        },
        "new_vlm_calls": 0,
    }
