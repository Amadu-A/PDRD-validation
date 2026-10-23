# services/analysis-service/src/pdrd_analysis_service/domain/comparison_semantics.py

"""Смысл измерения отделяется от числа и от физической идентичности изделия."""

import re
from decimal import Decimal

from pdrd_analysis_service.domain.document_context import normalize_label

_NUMBER = r"\d+(?:[.,]\d+)?"


def diameter_definition(fact):
    """Не принимает наружный размер, стенку и DN за одну величину."""
    name = normalize_label(fact.property_name + " " + fact.property_type)
    raw = normalize_label(fact.value_raw)
    if re.search(r"толщин|wall_thickness", name):
        return "wall_thickness"
    if re.search(r"\bsdr\b", name):
        return "sdr"
    if re.search(r"условн|номиналь|nominal|^\s*(?:dn|ду)", name + " " + raw):
        return "nominal_diameter"
    if re.match(r"(?:dn|ду)\s*\d", raw):
        return "nominal_diameter"
    if re.search(r"наружн|outer_diameter", name) or re.match(r"[ø⌀ф]\s*\d", raw):
        return "outer_diameter"
    if re.search(r"диаметр|diameter", name):
        value = re.escape(raw).replace(r"\.", "[.,]")
        if re.search(
            r"\b(?:dn|ду)\s*" + value + r"(?![\d.,])", fact.evidence_text, re.I
        ):
            return "nominal_diameter"
        if re.search(r"[ø⌀ф]\s*" + value + r"(?![\d.,])", fact.evidence_text, re.I):
            return "outer_diameter"
    return ""


def compatible_diameter_interval(facts):
    """Совпадение с явно записанным диапазоном не является противоречием.

    Не смешивает DN и наружный диаметр и не выводит диапазон из двух размеров
    типоразмера. Все наблюдения должны иметь одно доказанное определение.
    """
    definitions = {diameter_definition(fact) for fact in facts}
    if len(facts) < 2 or definitions not in ({"nominal_diameter"}, {"outer_diameter"}):
        return False
    intervals = []
    ranged = False
    for fact in facts:
        raw = normalize_label(fact.value_raw)
        match = re.fullmatch(
            rf"(?:dn|ду|[ø⌀ф])?\s*({_NUMBER})(?:\s*[-–—]\s*({_NUMBER}))?\s*(?:мм)?", raw
        )
        if match is None or fact.unit_raw not in {"", "мм", "mm"}:
            return False
        low = Decimal(match[1].replace(",", "."))
        high = Decimal((match[2] or match[1]).replace(",", "."))
        if high < low:
            return False
        ranged |= bool(match[2])
        intervals.append((low, high))
    return ranged and max(part[0] for part in intervals) <= min(
        part[1] for part in intervals
    )
