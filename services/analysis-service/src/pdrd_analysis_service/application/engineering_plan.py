# services/analysis-service/src/pdrd_analysis_service/application/engineering_plan.py

"""Предметные проверочные гипотезы по фактам текущего анализа, без экспертного benchmark."""

import re
from dataclasses import asdict

from pdrd_analysis_service.domain.analysis import DocumentContextSource, PageFacts
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    normalize_label,
    property_key,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    CLAIM_TYPES,
    assertion_from_facts,
    candidate_parameter_roles,
    candidate_property,
    engineering_object_key,
    read_assertion,
)
from pdrd_analysis_service.domain.engineering_facts import (
    explicit_design_parameters,
    explicit_system_purposes,
)


def engineering_plan(
    facts: PageFacts,
    text: str,
    sources: tuple[DocumentContextSource, ...] = (),
    *,
    page_number: int = 0,
) -> tuple[dict, ...]:
    """Привязывает проверочные вопросы к объекту, параметру, режиму и доступным контрдоказательствам."""
    result = []
    seen = set()
    for fact_index, fact in enumerate(
        (
            *facts.document_facts,
            *explicit_system_purposes(text),
            *explicit_design_parameters(text),
        ),
        1,
    ):
        object_name = fact.identifier or fact.subject_name
        key = (
            object_name,
            fact.property_name,
            fact.value_raw,
            fact.scope_operating_mode,
            fact.scope_segment,
            fact.scope_condition,
            fact.unit_raw,
            fact.table_id,
            fact.row_label,
        )
        if key in seen or not object_name or not fact.evidence_text:
            continue
        seen.add(key)
        related = tuple(
            source
            for source in sources
            if re.search(
                r"(?<![\w-])" + re.escape(object_name) + r"(?![\w-])",
                source.subject + " " + source.evidence_text + " " + source.text,
                re.I,
            )
        )
        question = (
            "Согласовано ли назначение этого же тега во всех переданных представлениях?"
            if fact.property_name == "Назначение системы"
            else "Где обосновано это принятое значение и согласованы ли объект, свойство, участок и режим? "
            "Проверь ссылку, расчётное приложение и продолжение таблицы. Не утверждай отсутствие во всём проекте "
            "по отсутствию на одном листе. Для конкретного незамкнутого расчётного шага допустим needs_review."
        )
        if fact.property_type == "sheet_number":
            question = "Согласованы ли печатный номер, шифр и порядок листов с перечнем и связанными частями документа?"
        elif fact.relation == "member_of":
            question = (
                "Подтверждена ли принадлежность этого элемента той же группе "
                "и согласован ли её состав в переданных материалах?"
            )
        elif fact.kind == "relationship":
            question = (
                "Подтверждены ли видимая связь, её назначение и принадлежность "
                "тем же объектам, участкам и условиям?"
            )
        elif fact.kind == "reference":
            question = (
                "Существует ли указанный лист, таблица или расчётное приложение "
                "и совпадают ли его шифр, название и назначение?"
            )
        elif fact.kind == "quantity":
            question = (
                "Соответствуют ли количество, перечень обозначений и размещение "
                "того же состава объектов в переданных представлениях?"
            )
        assertion = assertion_from_facts(
            (
                LocatedDocumentFact(
                    fact.source_pages[0] if fact.source_pages else max(1, page_number),
                    fact_index,
                    fact,
                ),
            )
        )
        result.append(
            {
                "hypothesis_id": f"engineering-{len(result) + 1}",
                "assertion": assertion.as_origin() if assertion else None,
                "object": object_name,
                "property": fact.property_name,
                "value": fact.value_raw,
                "unit": fact.unit_raw,
                "scope": {
                    "system": fact.scope_system,
                    "segment": fact.scope_segment,
                    "mode": fact.scope_operating_mode,
                    "condition": fact.scope_condition,
                },
                "evidence": fact.evidence_text,
                "question": question,
                "related_sources": [asdict(source) for source in related],
            }
        )
    return tuple(result)


def engineering_plan_for_prompt(
    plan: tuple[dict, ...], facts: PageFacts, page: int
) -> tuple[dict, ...]:
    """Ссылается на уже переданные факты вместо повторной отправки каждого полного наблюдения."""
    known = {(page, index): fact for index, fact in enumerate(facts.document_facts, 1)}
    result = []
    for item in plan:
        row = {
            key: value
            for key, value in item.items()
            if key not in {"assertion", "related_sources"}
        }
        row["related_source_ids"] = [
            source["source_id"] for source in item["related_sources"]
        ]
        assertion = read_assertion((item.get("assertion") or {},))
        if assertion is not None:
            payload = {
                key: value
                for key, value in assertion.as_origin().items()
                if key != "observations"
            }
            payload["observations"] = [
                {"page": observation.page, "fact_id": observation.fact_id}
                if known.get((observation.page, observation.index)) == observation.fact
                else asdict(observation)
                for observation in assertion.observations
            ]
            row["assertion"] = payload
        result.append(row)
    return tuple(result)


def bind_candidate_assertion(
    plan: tuple[dict, ...], candidate: dict, page: int, sources: tuple
) -> dict | None:
    """Связывает ответ с исходным предметом, не позволяя модели переписать факты плана."""
    text = f"{candidate.get('comment', '')} {candidate.get('evidence', '')}"
    prop = candidate_property(str(candidate.get("comment", ""))) or candidate_property(
        text
    )
    property_roots = {
        "Назначение системы": "назначени",
        "Диаметр": "диаметр|типоразмер",
        "Длина": "длин|протяж",
        "Толщина стенки": "толщин",
        "Расход воды": "расход",
        "Напор": "напор",
        "Абсолютная отметка": "отметк|высот",
    }
    linked = set(candidate.get("hypothesis_ids") or ())
    object_ref = normalize_label(str(candidate.get("object_ref") or ""))
    observations = []
    for item in plan:
        assertion = read_assertion((item.get("assertion") or {},))
        if assertion is None:
            continue
        if linked and item["hypothesis_id"] not in linked:
            continue
        if not linked and (
            not object_ref
            or not re.search(
                r"(?<!\w)" + re.escape(normalize_label(item["object"])) + r"(?!\w)",
                object_ref,
            )
            or normalize_label(item["property"]) != normalize_label(prop)
        ):
            continue
        pattern = property_roots.get(prop)
        actual_property = normalize_label(item["property"])
        # Новый предмет без известного синонима требует точного совпадения свойства.
        if prop and not (
            re.search(pattern, actual_property)
            if pattern is not None
            else actual_property == normalize_label(prop)
        ):
            continue
        roles = candidate_parameter_roles(str(candidate.get("comment", "")))
        if (
            candidate.get("claim_type") == "missing_justification"
            and roles
            and not any(role in normalize_label(item["property"]) for role in roles)
        ):
            continue
        observations.extend(assertion.observations)
    if not observations:
        return None
    if (
        len(
            {
                (engineering_object_key(item.fact), property_key(item.fact))
                for item in observations
            }
        )
        != 1
    ):
        return None
    # Для назначения системы добавляем только явно подписанные контрдоказательства D.
    if prop == "Назначение системы":
        identifier = observations[0].fact.identifier
        for source in sources:
            for fact in explicit_system_purposes(source.evidence_text):
                if fact.identifier == identifier:
                    observations.append(
                        LocatedDocumentFact(source.page, len(observations) + 1, fact)
                    )
    claim_type = candidate.get("claim_type")
    if claim_type not in CLAIM_TYPES:
        claim_type = (
            "missing_justification"
            if re.search(r"обоснован|без.*расч[её]т", text, re.I)
            else "contradiction"
        )
    assertion = assertion_from_facts(
        tuple(observations), claim_type=claim_type, include_related=True
    )
    return assertion.as_origin() if assertion else None


def candidate_engineering_context(facts: PageFacts, candidate: dict, page: int) -> dict:
    """Сохраняет относящиеся к кандидату факты и режимы для проверки вывода при финализации."""
    content = " ".join(
        str(candidate.get(key, "")) for key in ("object_ref", "comment", "evidence")
    )
    related = []
    for index, fact in enumerate(facts.document_facts, 1):
        identifier = fact.identifier or fact.subject_name
        matches_object = identifier and re.search(
            r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", content, re.I
        )
        matches_quote = len(fact.evidence_text) >= 8 and fact.evidence_text in str(
            candidate.get("evidence", "")
        )
        if matches_object or matches_quote:
            related.append({"fact_id": f"p{page:04d}-f{index:04d}", **asdict(fact)})
    return {"page": page, "facts": related}


def quantity_checks(facts: PageFacts) -> tuple[dict, ...]:
    """Передаёт проверяемые величины со всеми условиями вместо свободного пересчёта моделью."""
    from pdrd_analysis_service.domain.engineering_claims import extract_quantities

    result = []
    for fact in facts.document_facts:
        values = extract_quantities(f"{fact.value_raw} {fact.unit_raw}")
        if len(values) != 1:
            continue
        value = values[0]
        result.append(
            {
                "object": fact.identifier,
                "property": fact.property_name,
                "row": fact.row_label,
                "column": fact.column_label,
                "table": fact.table_id,
                "raw": fact.value_raw,
                "unit": fact.unit_raw,
                "dimension": value.dimension,
                "base_unit": {"flow": "м³/ч", "length": "м", "pressure": "Па"}[
                    value.dimension
                ],
                "normalized_value": str(value.value),
                "rounding_tolerance": str(value.tolerance),
                "mode": fact.scope_operating_mode,
                "segment": fact.scope_segment,
                "condition": fact.scope_condition,
                "continuation": fact.continuation_marker,
                "evidence": fact.evidence_text,
            }
        )
    return tuple(result)
