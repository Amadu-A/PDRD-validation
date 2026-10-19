# services/analysis-service/src/pdrd_analysis_service/domain/evidence_audit.py

"""Проверка предмета сравнения и буквальных оснований без модели и инфраструктуры."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace

from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
    normalize_label,
    normalized_value,
    property_key,
    scope_key,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    EngineeringAssertion,
    engineering_object_key,
)


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """Результат проверки одной исходной записи, включая ограничение визуального чтения."""

    fact_id: str
    page: int
    status: str
    reason_code: str

    @property
    def question_anchor(self) -> bool:
        """Разрешает осторожный вопрос при конкретном наблюдении, сохраняя предел его доказанности."""
        return self.status == "supported" or self.reason_code in {
            "visual_evidence_not_independently_verified",
            "condition_not_bound_to_quote",
            "object_binding_ambiguous",
        }


@dataclass(frozen=True, slots=True)
class EvidenceAudit:
    """Различает доказанный предмет, конкретный вопрос и внутреннюю диагностику."""

    status: str
    disposition: str
    reason_code: str
    bindings: tuple[EvidenceBinding, ...]

    def as_dict(self) -> dict:
        """Сохраняет все проверенные стороны, не сокращая исходные наблюдения."""
        return asdict(self)


def literal_key(text: str) -> str:
    """Уравнивает только запись чисел и знака умножения, без подмены инженерного смысла."""
    text = re.sub(r"(?<=\d),(?=\d)", ".", normalize_label(text)).replace("³", "3")
    text = re.sub(r"(?<=\d)\s*[xх×*]\s*(?=\d)", "x", text)
    text = re.sub(r"\b(dn|ду)\s+(?=\d)", r"\1", text)
    return re.sub(r"(?<=[ø⌀])\s+(?=\d)", "", text)


def literal_contains(text: str, value: str) -> bool:
    """Не принимает часть другого тега или числа за подтверждение исходного значения."""
    value = literal_key(value)
    return bool(value) and bool(
        re.search(
            r"(?<![\w\-])(?<!\d\.)" + re.escape(value) + r"(?![\w\-]|\.\d)",
            literal_key(text),
        )
    )


def bind_evidence(
    item: LocatedDocumentFact, page_texts: dict[int, str]
) -> EvidenceBinding:
    """Проверяет страницу, цитату, объект, значение, единицу и явно заявленные условия."""
    fact = item.fact

    def result(status: str, reason: str) -> EvidenceBinding:
        return EvidenceBinding(item.fact_id, item.page, status, reason)

    if not engineering_object_key(fact) or not property_key(fact):
        return result("unidentified", "object_or_property_unidentified")
    if fact.source_pages and item.page not in fact.source_pages:
        return result("counterevidence", "physical_page_mismatch")
    text = page_texts.get(item.page)
    if text is None:
        return result("source_unavailable", "physical_page_unavailable")
    quote = fact.evidence_text
    if fact.source_fragments:
        if not all(
            isinstance(part.get("page"), int)
            and literal_key(part.get("text", ""))
            and literal_key(part["text"])
            in literal_key(page_texts.get(part["page"], ""))
            for part in fact.source_fragments
        ):
            return result("counterevidence", "source_fragment_unavailable")
        text = " ".join(part["text"] for part in fact.source_fragments)
    if not quote or literal_key(quote) not in literal_key(text):
        return result(
            "incomplete",
            "visual_evidence_not_independently_verified"
            if fact.visual_regions
            else "quote_not_on_physical_page",
        )
    native_table = (
        fact.extraction_method == "pdf_table" and fact.column_binding == "pdf_geometry"
    )
    native_flow = (
        fact.extraction_method == "pdf_text"
        and fact.column_binding == "pdf_text_header"
        and fact.subject_type == "flow_consumer"
    )
    anchors = " ".join(
        (
            quote,
            fact.row_label if native_table or native_flow else "",
            fact.column_label if native_table or native_flow else "",
        )
    )
    object_bound = literal_contains(anchors, fact.identifier)
    if not object_bound and not (
        fact.visual_regions and literal_contains(text, fact.identifier)
    ):
        return result("counterevidence", "object_not_in_quote")
    clauses = [
        part
        for part in re.split(r"[;\n]", quote)
        if literal_contains(part, fact.identifier)
    ]
    value_quote = (
        " ".join(clauses) if clauses and not (native_table or native_flow) else quote
    )
    numeric = bool(re.fullmatch(r"\d+(?:[.,]\d+)?", fact.value_raw))
    inline_quantity = bool(
        numeric
        and fact.unit_raw
        and re.search(
            r"(?<![\w.\-])(?:dn|ду|[ø⌀ф])?\s*"
            + re.escape(literal_key(fact.value_raw))
            + r"\s*"
            + re.escape(literal_key(fact.unit_raw))
            + r"(?!\w)",
            literal_key(value_quote),
            re.I,
        )
    )
    literal_value = literal_contains(value_quote, fact.value_raw)
    literal_value = literal_value or inline_quantity
    if "диаметр" in normalize_label(fact.property_name) and re.fullmatch(
        r"\d+(?:[.,]\d+)?", fact.value_raw
    ):
        literal_value = literal_value or bool(
            re.search(
                r"(?:\b(?:dn|ду)|[ø⌀ф])\s*"
                + re.escape(literal_key(fact.value_raw))
                + r"(?![\d.])",
                literal_key(value_quote),
                re.I,
            )
        )
    if property_key(fact).endswith(":system_purpose"):
        purpose = normalized_value(fact)
        literal_value = literal_value or (
            purpose is not None
            and purpose == normalized_value(replace(fact, value_raw=value_quote))
        )
    if "толщин" in normalize_label(fact.property_name):
        literal_value = literal_value or bool(
            re.search(
                r"[x]\s*" + re.escape(literal_key(fact.value_raw)) + r"(?![\d.])",
                literal_key(value_quote),
            )
        )
    if not literal_value:
        return result("counterevidence", "value_not_in_quote")
    if (
        numeric
        and fact.unit_raw
        and not (native_table or native_flow)
        and not inline_quantity
    ):
        return result("incomplete", "unit_not_bound_to_quote")
    if fact.unit_raw and not literal_contains(anchors, fact.unit_raw):
        # Родственные записи единицы допускаются только при буквальном заголовке.
        unit = literal_key(fact.unit_raw).replace("³", "3").replace(" ", "")
        unit_text = text if native_flow else anchors
        if unit not in literal_key(unit_text).replace("³", "3").replace(" ", ""):
            return result("incomplete", "unit_not_bound_to_quote")
    label = normalize_label(fact.property_name)
    markers = (
        (r"толщин", r"стен|[xх×*]\s*\d"),
        (r"диаметр", r"диаметр|\b(?:dn|ду)\s*\d|[ø⌀]|\bф\s*\d"),
        (r"длин|протяж", r"длин|протяж|\bl\s*="),
        (r"расход", r"расход|л/с|м[3³]/ч"),
        (r"напор", r"напор"),
        (r"назначени|system_purpose", r"водоснабж|водопровод|водоотвед|канализац"),
    )
    for prop, evidence in markers:
        if re.search(prop, label) and not re.search(evidence, anchors, re.I):
            return result("incomplete", "property_not_bound_to_quote")
    for scope in (
        fact.scope_system,
        fact.scope_location,
        fact.scope_segment,
        fact.scope_operating_mode,
        fact.scope_condition,
    ):
        if scope and not literal_contains(anchors, scope):
            return result("incomplete", "condition_not_bound_to_quote")
    return (
        result("supported", "literal_evidence_bound")
        if object_bound
        else result("incomplete", "object_binding_ambiguous")
    )


def audit_assertion(
    assertion: EngineeringAssertion, *, page_texts: dict[int, str]
) -> EvidenceAudit:
    """Подтверждает сравнение лишь одного свойства одного объекта в доказанных условиях."""
    facts = assertion.observations
    bindings = tuple(bind_evidence(item, page_texts) for item in facts)

    def result(status: str, disposition: str, reason: str) -> EvidenceAudit:
        return EvidenceAudit(status, disposition, reason, bindings)

    if not facts or not assertion.object_key or not assertion.property_key:
        return result("unidentified", "diagnostic", "object_or_property_unidentified")
    # Явная связь состава и последовательность листов не сводятся к равенству двух скаляров.
    structural = next(
        (
            group
            for group in discover_conflicts(facts, max_candidates=len(facts) + 1)
            if group.mismatch_type in {"inventory_composition", "sheet_sequence"}
            and len(group.facts) == len(facts)
            and all(item in group.facts for item in facts)
        ),
        None,
    )
    if structural is not None and all(
        binding.status == "supported" for binding in bindings
    ):
        return result("supported", "supported", "explicit_" + structural.mismatch_type)
    objects = {engineering_object_key(item.fact) for item in facts}
    props = {property_key(item.fact) for item in facts}
    if objects != {assertion.object_key} or props != {assertion.property_key}:
        return result("unidentified", "diagnostic", "different_objects_or_properties")
    if (
        assertion.claim_type == "contradiction"
        and len({scope_key(item.fact) for item in facts}) > 1
    ):
        return result("incomplete", "diagnostic", "different_conditions")
    roles = {
        tuple(
            re.findall(
                r"гарантирован\w*|требуем\w*|средн\w*|максим\w*|миним\w*|водопотреб\w*|водоотвед\w*",
                normalize_label(
                    item.fact.property_name
                    + " "
                    + item.fact.column_label
                    + " "
                    + item.fact.row_label
                    + " "
                    + item.fact.table_title
                ),
            )
        )
        for item in facts
    }
    if assertion.claim_type == "contradiction" and len(roles) > 1:
        return result("incomplete", "diagnostic", "different_parameter_roles")
    supported = tuple(
        item
        for item, binding in zip(facts, bindings, strict=True)
        if binding.status == "supported"
    )
    if len(supported) != len(facts):
        question_anchors = tuple(
            binding for binding in bindings if binding.question_anchor
        )
        status = next(
            (
                state
                for state in ("counterevidence", "source_unavailable", "unidentified")
                if any(binding.status == state for binding in bindings)
            ),
            "incomplete",
        )
        return result(
            status,
            "question" if len(question_anchors) >= 2 else "diagnostic",
            "incomplete_comparison_evidence",
        )
    if assertion.claim_type == "missing_justification":
        return result("incomplete", "question", "justification_not_established")
    if assertion.claim_type != "contradiction":
        return result(
            "incomplete", "question", "conclusion_not_programmatically_verified"
        )
    if len(facts) < 2:
        return result("incomplete", "diagnostic", "second_comparison_value_unavailable")
    if all(
        re.search(r"сейсм|seismic", item.fact.property_name, re.I) for item in facts
    ):
        intervals = [
            re.fullmatch(
                r"\s*(\d+(?:[.,]\d+)?)(?:\s*[-–—]\s*(\d+(?:[.,]\d+)?))?\s*",
                item.fact.value_raw,
            )
            for item in facts
        ]
        if all(intervals):
            lows = [float(match[1].replace(",", ".")) for match in intervals]
            highs = [
                float((match[2] or match[1]).replace(",", ".")) for match in intervals
            ]
            if max(lows) <= min(highs):
                return result(
                    "counterevidence", "diagnostic", "compatible_seismic_range"
                )
        if not all(scope_key(item.fact)[5] for item in facts):
            return result(
                "incomplete", "question", "seismic_design_conditions_not_established"
            )
    values = {normalized_value(item.fact) for item in facts}
    if None in values:
        return result("incomplete", "question", "value_semantics_unidentified")
    dimensions = {value[0] for value in values}
    if len(dimensions) > 1:
        return result("incomplete", "diagnostic", "different_parameter_definitions")
    if len(values) == 1:
        return result(
            "counterevidence",
            "diagnostic",
            "equivalent_values_or_compatible_descriptions",
        )
    if "count" in dimensions and any(
        not item.fact.table_id or not item.fact.row_label for item in facts
    ):
        return result("incomplete", "diagnostic", "specification_group_not_established")
    if any(item.fact.subject_type == "unclassified" for item in facts):
        return result("unidentified", "question", "object_type_not_established")
    from pdrd_analysis_service.domain.physical_identity import comparison_identity

    identity = comparison_identity(facts)
    if identity in {
        "different_object_kinds",
        "different_value_roles",
        "different_physical_objects",
    }:
        return result("counterevidence", "diagnostic", identity)
    if identity == "physical_identity_unresolved":
        return result("incomplete", "question", "same_physical_segment_not_established")
    return result(
        "supported", "supported", "same_object_property_conditions_with_distinct_values"
    )


def diagnostic_language(text: str) -> bool:
    """Узнаёт язык реализации конвейера, а не тексты конкретных контрольных замечаний."""
    return bool(
        re.search(
            r"\b(?:engineering_context|origin_assertions|candidate_id|evidence|value_raw|scope_segment)\b"
            r"|(?:неполнот|недостат|отсутств|нет)\w*[^.!?]{0,50}извлеч[её]н\w*\s+факт"
            r"|извлеч[её]н\w*\s+факт[^.!?]{0,50}(?:непол|недостат|отсутств)",
            text,
            re.I,
        )
    )
