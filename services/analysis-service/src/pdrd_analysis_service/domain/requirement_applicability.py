# services/analysis-service/src/pdrd_analysis_service/domain/requirement_applicability.py

"""Доказательная применимость простых числовых N/T/U-требований к точному объекту."""

import re
from dataclasses import dataclass, replace

from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    normalize_label,
    property_key,
)
from pdrd_analysis_service.domain.engineering_assertion import engineering_object_key
from pdrd_analysis_service.domain.engineering_calculations import (
    comparison_tolerance,
    scalar,
)
from pdrd_analysis_service.domain.engineering_checks import (
    EngineeringCheck,
    literal_fact_value,
)


@dataclass(frozen=True, slots=True)
class RequirementEvidence:
    """Переданный источник; тип и принадлежность задаёт сценарий, а не модель."""

    kind: str
    source_id: str
    text: str
    page: int | str | None


def check_requirement(
    item: LocatedDocumentFact, source: RequirementEvidence
) -> EngineeringCheck:
    """Подтверждает только явный объект, свойство, порог и проверенные условия."""
    fact = item.fact
    base = {
        "rule": "requirement_applicability",
        "object_key": engineering_object_key(fact),
        "property_key": property_key(fact),
        "observations": (item,),
        "source_ids": (source.source_id,),
    }
    if (
        source.kind not in {"N", "T", "U"}
        or not source.source_id
        or source.page is None
        or not str(source.page).isdigit()
        or int(source.page) < 1
        or not source.source_id.startswith(source.kind)
        or not literal_fact_value(item)
    ):
        return EngineeringCheck(
            status="not_evaluable",
            reason_code="unverified_requirement_source",
            missing_data=("located_N_T_U_source",),
            **base,
        )
    tag = normalize_label(fact.identifier)
    prop = normalize_label(fact.property_name)
    sentences = re.split(r"[;\n]|(?<=[.!?])\s+(?=[А-ЯA-Z])", source.text)
    matching = [
        normalize_label(sentence)
        for sentence in sentences
        if tag
        and re.search(
            r"(?<!\w)" + re.escape(tag) + r"(?!\w)", normalize_label(sentence)
        )
    ]
    text = " ".join(matching) if len(matching) == 1 else ""
    if (
        not tag
        or not prop
        or not re.search(r"(?<!\w)" + re.escape(tag) + r"(?!\w)", text)
    ):
        return EngineeringCheck(
            status="not_evaluable",
            reason_code="object_applicability_not_established",
            missing_data=("explicit_object_applicability",),
            **base,
        )
    if re.search(r"кроме|за исключением|если|\bпри\b|устарел|отменен|заменен", text):
        return EngineeringCheck(
            status="not_evaluable",
            reason_code="conditional_requirement",
            missing_data=("exceptions_and_conditions",),
            **base,
        )
    # Условия не выводятся из похожего абзаца; все известные ограничения источника должны совпасть.
    modes = {
        mode for mode in ("пожарный", "аварийный", "штатный", "рабочий") if mode in text
    }
    scope = normalize_label(fact.scope_operating_mode + " " + fact.scope_condition)
    if modes and not all(mode in scope for mode in modes):
        return EngineeringCheck(
            status="not_evaluable",
            reason_code="requirement_mode_not_established",
            missing_data=("same_operating_mode",),
            **base,
        )
    pattern = (
        re.escape(prop)
        + r"\s*(?P<operator>не менее|не более|>=|<=|≥|≤)\s*(?P<value>\d+(?:[.,]\d+)?)\s*"
        r"(?P<unit>м³/ч|м3/ч|л/с|м³|м3|кпа|мпа|квт|мм|шт\.?|м)(?!\w)"
    )
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        return EngineeringCheck(
            status="not_evaluable",
            reason_code="exact_requirement_constraint_not_observed",
            missing_data=("one_explicit_property_constraint",),
            **base,
        )
    match = matches[0]
    tags = re.findall(
        r"(?<!\w)[a-zа-я]{1,4}\d+(?:[.-]\d+)*(?!\w)", text[: match.start()]
    )
    if tags and tags[-1] != tag:
        return EngineeringCheck(
            status="not_evaluable",
            reason_code="constraint_belongs_to_other_object",
            missing_data=("same_object_constraint",),
            **base,
        )
    project = scalar(fact)
    required = scalar(replace(fact, value_raw=match["value"], unit_raw=match["unit"]))
    if not project or not required or project.dimension != required.dimension:
        return EngineeringCheck(
            status="not_evaluable",
            reason_code="incompatible_requirement_units",
            missing_data=("compatible_units",),
            **base,
        )
    tolerance = comparison_tolerance(project, required)
    minimum = match["operator"] in {"не менее", ">=", "≥"}
    mismatch = (
        project.value + tolerance < required.value
        if minimum
        else project.value - tolerance > required.value
    )
    return EngineeringCheck(
        status="confirmed" if mismatch else "no_finding",
        reason_code="explicit_requirement_violated"
        if mismatch
        else "explicit_requirement_satisfied",
        calculation={
            "project": str(project.value),
            "limit": str(required.value),
            "operator": match["operator"],
            "dimension": project.dimension,
            "source_kind": source.kind,
            "source_quote": match[0],
            "source_page": source.page,
            "tolerance": str(tolerance),
        },
        **base,
    )
