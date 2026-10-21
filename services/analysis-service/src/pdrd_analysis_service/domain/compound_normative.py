# services/analysis-service/src/pdrd_analysis_service/domain/compound_normative.py

"""Ограниченные составные нормы: условия, таблицы и явно заданные формулы.

Правила читаются из буквального источника. Неизвестный заголовок, исключение,
ссылка или недоказанная связь объектов возвращают ограничение, а не нарушение.
"""

import ast
import re
from dataclasses import dataclass, replace
from decimal import Decimal, DecimalException

from pdrd_analysis_service.domain.document_context import normalize_label, scope_key
from pdrd_analysis_service.domain.engineering_assertion import engineering_object_key
from pdrd_analysis_service.domain.engineering_calculations import scalar
from pdrd_analysis_service.domain.evidence_audit import bind_evidence

_UNIT = r"м[³3]/(?:ч|сут)|л/с|м/с|м[³3]|кПа|МПа|мм|шт\.?|ч|м"
_NUMBER = r"\d+(?:[.,]\d+)?"
_LIMIT = re.compile(
    r"(?P<property>[^,;:=]+?)\s+(?P<op>не менее|не более|свыше|до)\s*(?P<value>"
    + _NUMBER
    + r")\s*(?P<unit>"
    + _UNIT
    + r")(?!\w)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class CompoundEvaluation:
    """Результат проверки одного физического объекта и одного режима."""

    rule: str
    result: str
    reason: str
    observations: tuple
    required_data: tuple[str, ...] = ()
    comparison: dict | None = None


def _name(value):
    """Сопоставляет имена свойств, сохраняя внутренний/наружный/номинальный диаметр."""
    value = normalize_label(value).strip()
    for pattern, canonical in (
        (r"\bобъем(?:а|е|ом|ы|ов)?\b", "объем"),
        (r"\bрасход(?:а|е|ом|ы|ов)?\b", "расход"),
        (r"\bдавлени(?:е|я|и|ем)\b", "давление"),
        (r"\bпродолжительност(?:ь|и|ью)\b", "продолжительность"),
        (r"\bздани(?:е|я|и|ем|й)\b", "здание"),
        (r"\bдиаметр(?:а|е|ом|ы|ов)?\b", "диаметр"),
    ):
        value = re.sub(pattern, canonical, value)
    return value


def _matching(items, name):
    """Одинаковый предмет подтверждается названием, а не близостью чисел."""
    target = _name(name)
    if re.search(r"категор|класс|наличие", target):

        def terms(text):
            return {
                word[:5] for word in re.findall(r"[а-яa-z]+", text) if word != "наличие"
            }

        return tuple(
            item
            for item in items
            if terms(_name(item.fact.property_name)) == terms(target)
        )
    return tuple(item for item in items if _name(item.fact.property_name) == target)


def _unique(items):
    """Геометрия фактов может содержать dict; уникальность задаётся исходным fact_id."""
    return tuple({item.fact_id: item for item in items}.values())


def _quantity(items, name, unit):
    """Приводит единственное наблюдённое значение к явно указанной единице нормы."""
    found = _matching(items, name)
    values = []
    for item in found:
        value = scalar(item.fact)
        factor = scalar(replace(item.fact, value_raw="1", unit_raw=unit))
        if (
            value is None
            or factor is None
            or value.dimension != factor.dimension
            or factor.value == 0
        ):
            return None
        values.append(
            (value.value / factor.value, value.tolerance / abs(factor.value), item)
        )
    if not values or len({(value, tolerance) for value, tolerance, _ in values}) != 1:
        return None
    return values[0][0], values[0][1], tuple(item for _, _, item in values)


def _condition(text, items):
    """Вычисляет конъюнкцию только известных числовых и классификационных предпосылок."""
    missing, used = [], []
    false = False
    for part in re.split(r"\s+и\s+|;", text):
        part = part.strip(" ,.")
        limit = _LIMIT.fullmatch(part)
        if limit:
            quantity = _quantity(items, limit["property"], limit["unit"])
            if quantity is None:
                missing.append(limit["property"])
                continue
            value, tolerance, observations = quantity
            boundary = Decimal(limit["value"].replace(",", "."))
            used.extend(observations)
            op = limit["op"].casefold()
            low, high = value - tolerance, value + tolerance
            proven = {
                "до": high <= boundary,
                "свыше": low > boundary,
                "не менее": low >= boundary,
                "не более": high <= boundary,
            }[op]
            disproven = {
                "до": low > boundary,
                "свыше": high <= boundary,
                "не менее": high < boundary,
                "не более": low > boundary,
            }[op]
            if disproven:
                false = True
            elif not proven:
                missing.append("точность условия: " + part)
            continue
        category = re.fullmatch(
            r"(.+?)\s+(категории|класса)\s+([А-ЯA-Z0-9IV.-]+)", part, re.I
        )
        if category:
            label = "категория" if category[2].casefold() == "категории" else "класс"
            candidates = _matching(items, category[1] + " " + label)
            values = {_name(item.fact.value_raw) for item in candidates}
            if len(values) == 1:
                used.extend(candidates)
                false |= next(iter(values)) != _name(category[3])
                continue
        missing.append(part)
    return (
        "not_applicable"
        if false
        else "applicability_unknown"
        if missing
        else "applicable",
        tuple(missing),
        tuple(used),
    )


def _bound(target, items, condition=""):
    """Сначала проверяет все условия, затем значение единственного целевого параметра."""
    match = _LIMIT.fullmatch(target.strip(" .;"))
    if match is None:
        return CompoundEvaluation(
            "conditional_bound",
            "not_supported",
            "unsupported_conditional_constraint",
            items,
        )
    state, missing, used = (
        _condition(condition, items) if condition else ("applicable", (), ())
    )
    if state != "applicable":
        return CompoundEvaluation(
            "conditional_bound",
            state,
            "condition_false"
            if state == "not_applicable"
            else "conditions_not_observed",
            (*used, *items),
            missing,
        )
    quantity = _quantity(items, match["property"], match["unit"])
    if quantity is None:
        return CompoundEvaluation(
            "conditional_bound",
            "insufficient_data",
            "target_parameter_not_bound",
            items,
            (match["property"],),
        )
    value, tolerance, observations = quantity
    limit = Decimal(match["value"].replace(",", "."))
    minimum = match["op"].casefold() == "не менее"
    if match["op"].casefold() not in {"не менее", "не более"}:
        return CompoundEvaluation(
            "conditional_bound", "not_supported", "ambiguous_target_operator", items
        )
    violated = value + tolerance < limit if minimum else value - tolerance > limit
    return CompoundEvaluation(
        "conditional_bound",
        "violated" if violated else "satisfied",
        "conditional_requirement_checked",
        _unique((*observations, *used)),
        comparison={
            "project": str(value),
            "limit": str(limit),
            "operator": match["op"],
            "unit": match["unit"],
            "conditions": condition,
            "tolerance": str(tolerance),
        },
    )


def _formula_interval(expression, variables):
    """Распространяет интервалы через операции; деление через ноль не вычисляется.

    Повтор переменной расширяет интервал, но не создаёт ложного нарушения.
    Проверка одних углов диапазона для немонотонного выражения недостаточна.
    """
    clean = expression.strip().replace(",", ".")
    if len(clean) > 240:
        return None
    try:
        tree = ast.parse(clean, mode="eval")
        if len(list(ast.walk(tree))) > 70:
            return None

        def visit(node):
            if isinstance(node, ast.Name) and node.id in variables:
                value, tolerance, _ = variables[node.id]
                return value - tolerance, value + tolerance
            if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
                value = Decimal(ast.get_source_segment(clean, node))
                return value, value
            if isinstance(node, ast.UnaryOp) and isinstance(
                node.op, (ast.USub, ast.UAdd)
            ):
                low, high = visit(node.operand)
                return (-high, -low) if isinstance(node.op, ast.USub) else (low, high)
            if isinstance(node, ast.BinOp):
                a, b = visit(node.left)
                c, d = visit(node.right)
                if isinstance(node.op, ast.Add):
                    return a + c, b + d
                if isinstance(node.op, ast.Sub):
                    return a - d, b - c
                if isinstance(node.op, ast.Mult):
                    values = (a * c, a * d, b * c, b * d)
                    return min(values), max(values)
                if isinstance(node.op, ast.Div) and not c <= 0 <= d:
                    values = (a / c, a / d, b / c, b / d)
                    return min(values), max(values)
            raise ValueError("Неподдерживаемая интервальная операция.")

        result = visit(tree.body)
        return (
            result
            if all(v.is_finite() and abs(v) < Decimal("1e30") for v in result)
            else None
        )
    except (SyntaxError, ValueError, TypeError, DecimalException, RecursionError):
        return None


def _formula(text, items):
    """Вычисляет буквальную формулу только с явными расшифровками и единицами всех переменных."""
    formula = re.search(
        r"(?m)^\s*([A-Za-z])\s*=\s*([A-Za-z0-9.,()+*/ ×·−-]+)\s*$", text
    )
    if formula is None:
        return CompoundEvaluation(
            "formula",
            "not_supported",
            "formula_not_structured",
            items,
            ("формула и расшифровки переменных",),
        )
    expression = formula[2].replace("×", "*").replace("·", "*").replace("−", "-")
    if formula[1] in re.findall(r"[A-Za-z]", expression):
        return CompoundEvaluation(
            "formula", "not_supported", "implicit_formula_not_supported", items
        )
    names = tuple(dict.fromkeys((formula[1], *re.findall(r"[A-Za-z]", expression))))
    definitions, selected = {}, []
    for name in names:
        definition = re.search(
            r"(?m)^\s*"
            + name
            + r"\s*[-—–]\s*([^,;\n]+),\s*("
            + _UNIT
            + r")\s*[.;]?\s*$",
            text,
            re.I,
        )
        if definition is None:
            return CompoundEvaluation(
                "formula",
                "insufficient_data",
                "formula_variable_unbound",
                items,
                ("расшифровка " + name,),
            )
        quantity = _quantity(items, definition[1], definition[2])
        if quantity is None:
            return CompoundEvaluation(
                "formula",
                "insufficient_data",
                "formula_input_not_observed",
                items,
                (definition[1],),
            )
        definitions[name] = quantity
        selected.extend(quantity[2])
    interval = _formula_interval(expression, definitions)
    if interval is None:
        return CompoundEvaluation(
            "formula", "not_supported", "unsupported_or_singular_formula", items
        )
    actual, tolerance, _ = definitions[formula[1]]
    violated = actual + tolerance < interval[0] or actual - tolerance > interval[1]
    return CompoundEvaluation(
        "formula",
        "violated" if violated else "satisfied",
        "literal_formula_checked",
        _unique(selected),
        comparison={
            "expression": formula[0].strip(),
            "project": str(actual),
            "calculated_min": str(interval[0]),
            "calculated_max": str(interval[1]),
            "variable_values": {
                name: str(value[0]) for name, value in definitions.items()
            },
        },
    )


def evaluate_compound_requirement(row, facts, page_texts):
    """Возвращает None для прежнего простого правила, остальные результаты сохраняет явно."""
    text = row.get("source_text", "")
    conditional = re.search(
        r"\bпри\s+(.+?),\s*((?:расход|напор|давление|скорость|объ[её]м|количество)[^;\n]+?)(?=[.!](?:\s|$)|;|\n|$)",
        text,
        re.I,
    )
    table = row.get("verification_type") == "table"
    formula = row.get("verification_type") == "formula"
    presence = re.search(
        r"(?:необходимо|следует)\s+предусмотреть\s+([^.;\n]+)", text, re.I
    )
    calculation = row.get("verification_type") == "calculation_required"
    if not (conditional or table or formula or presence or calculation):
        return None
    # Только факты одного явно обозначенного объекта и условий, подтверждённые оригиналом.
    groups = {}
    family = re.sub(
        r"(?:ами|ями|ов|ам|ям|ах|ях|ы|и|а|я)$",
        "",
        normalize_label(row.get("object_type", "")),
    )
    for item in facts:
        if (
            not item.fact.identifier
            or not family
            or family not in normalize_label(item.fact.evidence_text)
        ):
            continue
        # Упоминание гидранта где-то в тексте системы не делает систему гидрантом.
        # Нужна явная связь класса с тем же тегом, либо структурное имя объекта.
        described = normalize_label(
            " ".join(
                (item.fact.subject_name, item.fact.object_kind, item.fact.subject_type)
            )
        )
        tagged = re.search(
            re.escape(family)
            + r"\w*\s+[«\"']?"
            + re.escape(normalize_label(item.fact.identifier))
            + r"(?![\w-])",
            normalize_label(item.fact.evidence_text),
        )
        if family not in described and tagged is None:
            continue
        if bind_evidence(item, page_texts).status != "supported":
            continue
        groups.setdefault(
            (engineering_object_key(item.fact), scope_key(item.fact)), []
        ).append(item)
    if not groups:
        return (
            CompoundEvaluation(
                "compound",
                "insufficient_data",
                "project_object_not_bound",
                (),
                tuple(row.get("required_parameters", [])),
            ),
        )
    results = []
    for values in groups.values():
        items = tuple(values)
        if row.get("exceptions"):
            results.append(
                CompoundEvaluation(
                    "compound",
                    "applicability_unknown",
                    "exceptions_not_resolved",
                    items,
                    tuple(row["exceptions"]),
                )
            )
        elif conditional:
            results.append(_bound(conditional[2], items, conditional[1]))
        elif formula:
            results.append(_formula(text, items))
        elif presence:
            observed = _matching(items, "Наличие " + presence[1])
            states = {normalize_label(item.fact.value_raw) for item in observed}
            if len(states) != 1 or not states <= {
                "предусмотрен",
                "предусмотрена",
                "предусмотрено",
                "имеется",
                "отсутствует",
                "не предусмотрен",
                "не предусмотрена",
                "не предусмотрено",
            }:
                results.append(
                    CompoundEvaluation(
                        "required_component",
                        "insufficient_data",
                        "component_presence_not_bound",
                        items,
                        (presence[1],),
                    )
                )
            else:
                absent = next(iter(states)).startswith(("не ", "отсутств"))
                results.append(
                    CompoundEvaluation(
                        "required_component",
                        "violated" if absent else "satisfied",
                        "explicit_component_presence_checked",
                        observed,
                        comparison={
                            "component": presence[1],
                            "project": next(iter(states)),
                        },
                    )
                )
        elif calculation:
            adopted = tuple(
                item
                for item in items
                if scalar(item.fact) is not None
                and item.fact.property_type
                not in {"design_input", "calculation_reference"}
            )
            references = tuple(
                item
                for item in items
                if item.fact.property_type == "calculation_reference"
            )
            if not adopted:
                results.append(
                    CompoundEvaluation(
                        "required_calculation",
                        "insufficient_data",
                        "adopted_parameter_not_observed",
                        items,
                        tuple(row.get("required_parameters", [])),
                    )
                )
            else:
                missing = (
                    *row.get("required_parameters", []),
                    "исходные условия расчёта",
                    "связь расчёта с принятым параметром",
                )
                results.append(
                    CompoundEvaluation(
                        "required_calculation",
                        "insufficient_data" if references else "question",
                        "calculation_reference_requires_resolution"
                        if references
                        else "required_calculation_not_linked_in_checked_pages",
                        _unique((*adopted, *references)),
                        missing,
                        comparison={
                            "checked_pages": sorted(page_texts),
                            "missing_parameters": list(missing),
                        },
                    )
                )
        else:
            lines = [
                [
                    cell.strip()
                    for cell in re.split(r"\||\t|\s{2,}", line)
                    if cell.strip()
                ]
                for line in text.splitlines()
            ]
            # Направление ограничения должно быть напечатано, а не угадано по числу.
            header = next(
                (
                    i
                    for i, cells in enumerate(lines)
                    if len(cells) == 2
                    and re.search(r",\s*(?:" + _UNIT + r")$", cells[0], re.I)
                    and re.search(
                        r",\s*(?:" + _UNIT + r"),?\s+не (?:менее|более)$",
                        cells[1],
                        re.I,
                    )
                ),
                None,
            )
            found = []
            if header is not None:
                left, right = lines[header]
                prop, unit = left.rsplit(",", 1)
                bound_header = re.fullmatch(
                    r"(.+?),\s*(" + _UNIT + r"),?\s+(не менее|не более)", right, re.I
                )
                target, target_unit, operator = bound_header.groups()
                for cells in lines[header + 1 :]:
                    if len(cells) != 2 or not re.fullmatch(_NUMBER, cells[1]):
                        continue
                    state = _bound(
                        target
                        + " "
                        + operator
                        + " "
                        + cells[1]
                        + " "
                        + target_unit.strip(),
                        items,
                        prop + " " + cells[0] + " " + unit.strip(),
                    )
                    if state.result != "not_applicable":
                        found.append(
                            replace(
                                state,
                                rule="table_row",
                                comparison={
                                    **(state.comparison or {}),
                                    "table_row": cells,
                                },
                            )
                        )
            results.append(
                found[0]
                if len(found) == 1
                else CompoundEvaluation(
                    "table",
                    "not_supported",
                    "table_row_selection_not_proven",
                    items,
                    ("однозначная строка таблицы и её условия",),
                )
            )
    return tuple(results)
