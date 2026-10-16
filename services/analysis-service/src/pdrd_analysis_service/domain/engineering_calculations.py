# services/analysis-service/src/pdrd_analysis_service/domain/engineering_calculations.py

"""Ограниченная арифметика и совместимые величины без инженерных допущений."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, replace
from decimal import Decimal, DecimalException

from pdrd_analysis_service.domain.document_context import (
    DocumentFact,
    normalize_label,
    normalized_value,
)

_EXTRA_UNITS = {
    "м/с": ("velocity_ms", "1"),
    "m/s": ("velocity_ms", "1"),
    "м³": ("volume_m3", "1"),
    "м3": ("volume_m3", "1"),
    "л": ("volume_m3", ".001"),
    "ч": ("time_h", "1"),
    "час": ("time_h", "1"),
    "мин": ("time_h", "0.01666666666666666666666666667"),
    "с": ("time_h", "0.0002777777777777777777777777778"),
    "м³/сут": ("flow_day", "1"),
    "м3/сут": ("flow_day", "1"),
    "v": ("voltage_v", "1"),
    "в": ("voltage_v", "1"),
    "a": ("current_a", "1"),
    "а": ("current_a", "1"),
    "ма": ("current_a", ".001"),
}


@dataclass(frozen=True, slots=True)
class Scalar:
    """Значение с размерностью и абсолютной точностью исходной записи."""

    dimension: str
    value: Decimal
    tolerance: Decimal


def scalar(fact: DocumentFact) -> Scalar | None:
    """Не превращает диапазон, номинальный диаметр или текст в число."""
    raw = fact.value_raw.strip().replace(",", ".")
    if len(raw) > 40 or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw):
        return None
    unit = normalize_label(fact.unit_raw).replace(" ", "")
    number = Decimal(raw)
    additional = _EXTRA_UNITS.get(unit)
    if additional:
        dimension, factor = additional[0], Decimal(additional[1])
        value = number * factor
    else:
        parsed = normalized_value(fact)
        if parsed is None or parsed[0] in {
            "text",
            "nominal_diameter",
            "outer_diameter",
        }:
            return None
        dimension, value = parsed[0], Decimal(parsed[1])
        one = normalized_value(replace(fact, value_raw="1"))
        factor = abs(Decimal(one[1])) if one is not None else Decimal(1)
    precision = len(raw.partition(".")[2]) if "." in raw else 6
    return Scalar(dimension, value, Decimal("0.5") * Decimal(10) ** -precision * factor)


def arithmetic(expression: str) -> Decimal | None:
    """Вычисляет только короткие числовые +, −, ×, /; имена, вызовы и степени запрещены."""
    clean = (
        expression.replace(",", ".")
        .replace("×", "*")
        .replace("·", "*")
        .replace("−", "-")
    )
    if len(clean) > 240 or not re.fullmatch(r"[\d.\s()+*/-]+", clean):
        return None
    try:
        tree = ast.parse(clean.strip(), mode="eval")
        if len(list(ast.walk(tree))) > 70:
            return None

        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
                return Decimal(ast.get_source_segment(clean.strip(), node))
            if isinstance(node, ast.UnaryOp) and isinstance(
                node.op, (ast.UAdd, ast.USub)
            ):
                value = visit(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
            raise ValueError("Неподдерживаемая операция.")

        result = visit(tree.body)
        return result if result.is_finite() and abs(result) < Decimal("1e30") else None
    except (SyntaxError, ValueError, TypeError, DecimalException, RecursionError):
        return None


def comparison_tolerance(*values: Scalar, relative: str = ".000001") -> Decimal:
    """Учитывает округление источников и малый относительный допуск вычисления."""
    return sum((item.tolerance for item in values), Decimal(0)) + max(
        (abs(item.value) for item in values), default=Decimal(0)
    ) * Decimal(relative)


def interval(fact: DocumentFact) -> tuple[Scalar, Scalar] | None:
    """Читает только явно записанный закрытый диапазон одной характеристики."""
    match = re.fullmatch(
        r"([+-]?\d+(?:[.,]\d+)?)\s*(?:\.\.+|–|—)\s*([+-]?\d+(?:[.,]\d+)?)",
        fact.value_raw,
    )
    if match is None:
        return None
    low, high = (scalar(replace(fact, value_raw=value)) for value in match.groups())
    return (
        (low, high)
        if low and high and low.dimension == high.dimension and low.value <= high.value
        else None
    )
