# services/analysis-service/src/pdrd_analysis_service/domain/engineering_claims.py

"""Консервативная проверка явных арифметических утверждений модели.

Отбрасываются только доказуемо неверные сравнения совместимых единиц и явные
календарные длительности. Неясные условия и инженерная совместимость не угадываются.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

REVIEW_REASON_CODES = (
    "confirmed_evidence",
    "needs_engineer_review",
    "different_object",
    "different_property",
    "different_mode",
    "different_condition",
    "different_table_scope",
    "incomplete_evidence",
    "equivalent_units",
    "incomparable_diameter",
    "incomparable_seismic_map",
    "non_specific_claim",
    "false_arithmetic",
    "unsupported_conclusion",
    "extraction_is_not_evidence",
)

_NUMBER = r"\d+(?:[.,]\d+)?"
_UNITS = {
    "м": ("length", Decimal(1)),
    "мм": ("length", Decimal("0.001")),
    "кпа": ("pressure", Decimal(1000)),
    "мпа": ("pressure", Decimal(1000000)),
    "л/с": ("flow", Decimal("3.6")),
    "м³/ч": ("flow", Decimal(1)),
    "м3/ч": ("flow", Decimal(1)),
}
_COMPARISON = re.compile(
    rf"(?P<a>{_NUMBER})\s*(?P<ua>м³/ч|м3/ч|л/с|мм|мпа|кпа|м)\s*"
    rf"(?P<op>меньше|больше|ниже|выше|не превышает|превышает)\s*"
    rf"(?P<b>{_NUMBER})\s*(?P<ub>м³/ч|м3/ч|л/с|мм|мпа|кпа|м)\b",
    re.I,
)
_QUANTITY = re.compile(
    rf"(?<![\w.,])(?P<value>{_NUMBER})\s*(?P<unit>м\s*[³3]\s*/\s*(?:ч(?:ас)?|h)|л\s*/\s*(?:с(?:ек)?|s)|мм|мпа|кпа|м)(?!\w)",
    re.I,
)
_EQUIVALENCE = re.compile(
    r"(?<!не )\b(?:соответствует|эквивалент|равн|равен|составляет|получ(?:ается|аем|ится)|даёт|дает|"
    r"должн|пересч[её]т|корректно|правильно|то есть)|=|т\.\s*е\.",
    re.I,
)
_DIFFERENCE = re.compile(
    r"не соответству|не эквивалент|не совпада|несогласован|расхожд|различаются|противореч|несоответств|"
    r"неверн\w*\s+пересч|ошиб\w*\s+пересч|несовпад|разн\w*\s+диаметр|тогда как",
    re.I,
)
_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ),
        1,
    )
}
_PERIOD = re.compile(
    rf"(?:с\s+)?(?P<d1>\d{{1,2}})\s+(?P<m1>{'|'.join(_MONTHS)})\s*"
    rf"(?:—|–|-|по|до)\s*(?P<d2>\d{{1,2}})\s+(?P<m2>{'|'.join(_MONTHS)})"
    rf"\s+(?:составляет|равен|длится)\s+(?P<value>{_NUMBER})\s+месяц",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Quantity:
    """Число в общей размерности с погрешностью округления исходной записи."""

    value: Decimal
    dimension: str
    tolerance: Decimal
    unit: str
    start: int
    end: int
    quote_span: tuple[int, int] | None = None


def extract_quantities(text: str) -> tuple[Quantity, ...]:
    """Читает величины полного текста, сохраняя единицу и точность каждой записи."""
    quantities = []
    quotes = tuple(
        (match.start(), match.end()) for match in re.finditer(r'«[^»]*»|"[^"]*"', text)
    )
    for match in _QUANTITY.finditer(text):
        raw = match["value"].replace(",", ".")
        unit = re.sub(r"\s+", "", match["unit"].casefold())
        unit = (
            unit.replace("час", "ч")
            .replace("сек", "с")
            .replace("/h", "/ч")
            .replace("/s", "/с")
        )
        dimension, factor = _UNITS[unit]
        number = Decimal(raw)
        # Целое проектное значение не получает допуск в половину метра/литра.
        precision = len(raw.split(".")[1]) if "." in raw else 6
        quantities.append(
            Quantity(
                number * factor,
                dimension,
                Decimal("0.5") * Decimal(10) ** -precision * factor,
                unit,
                match.start(),
                match.end(),
                next(
                    (span for span in quotes if span[0] <= match.start() < span[1]),
                    None,
                ),
            )
        )
    return tuple(quantities)


def claim_fragments(text: str) -> tuple[str, ...]:
    """Разделяет самостоятельные претензии, не разрезая числа, цитаты и пересчёт."""
    # Союз отделяет другую претензию только при собственном предмете проверки.
    boundary = (
        r";\s*|(?<=[.!?])\s+(?=[А-ЯA-Z])|"
        r",?\s+\b(?:но|однако|кроме того|также|при этом)\s+"
        r"(?=(?:в\s+\w+\s+)?(?:обозначен|маркиров|модел|марк[аиу]|материал|комплект|позици|"
        r"спецификац|назначен|ссылк|нумерац|производител|характеристик|мощност|давлен|"
        r"напор|температур|диаметр|длин|толщин|напряжен|сечени|масса))"
    )
    # Текст цитат не участвует в поиске границ авторских утверждений.
    protected = re.sub(r'«[^»]*»|"[^"]*"', lambda m: "_" * len(m[0]), text)
    starts, result = 0, []
    for match in re.finditer(boundary, protected, re.I):
        result.append(text[starts : match.start()].strip())
        starts = match.end()
    result.append(text[starts:].strip())
    return tuple(part for part in result if part)


def filter_numeric_claims(text: str) -> tuple[str, tuple[dict, ...]]:
    """Удаляет только опровергнутые самостоятельные утверждения, сохраняя их для аудита."""
    kept, refuted = [], []
    for part in claim_fragments(text):
        reason = _false_numeric_claim(part)
        if reason:
            refuted.append(
                {
                    "text": part,
                    "reason": reason,
                    "normalized_quantities": [
                        {
                            "dimension": q.dimension,
                            "value": str(q.value),
                            "unit": q.unit,
                            "tolerance": str(q.tolerance),
                        }
                        for q in extract_quantities(part)
                    ],
                }
            )
        else:
            kept.append(part)
    if refuted:
        # «Исправить расчёт» — продолжение опровергнутого вывода, а не новая претензия.
        kept = [
            part
            for part in kept
            if re.search(
                r"обозначен|маркиров|модел|марк[аиу]|материал|комплект|позици|спецификац|"
                r"назначен|ссылк|нумерац|производител|характеристик|мощност|давлен|напор|"
                r"температур|диаметр|длин|толщин|напряжен|сечени|масса",
                part,
                re.I,
            )
            and re.search(
                r"не совпад|не соответств|расхожд|невер|ошиб|отсутств|не указан|различ|противореч",
                part,
                re.I,
            )
        ]
    return ("; ".join(kept) if refuted else text), tuple(refuted)


def false_numeric_claim(text: str) -> str | None:
    """Возвращает код доказуемо неверного утверждения либо оставляет его модели."""
    return next(
        (
            reason
            for part in claim_fragments(text)
            if (reason := _false_numeric_claim(part))
        ),
        None,
    )


def _false_numeric_claim(text: str) -> str | None:
    """Проверяет арифметику в пределах одной самостоятельной претензии."""
    # Защищаем цитату исходной ошибки, но проверяем вычисления автора вне цитаты.
    quantities = extract_quantities(text)
    pairs = list(pairwise(quantities))
    for index, right in enumerate(quantities):
        if index == 0 or not _EQUIVALENCE.search(
            text[quantities[index - 1].end : right.start]
        ):
            continue
        previous = next(
            (
                left
                for left in reversed(quantities[:index])
                if left.dimension == right.dimension and left.unit != right.unit
            ),
            None,
        )
        if previous is not None and (previous, right) not in pairs:
            pairs.append((previous, right))
    for left, right in pairs:
        if left.dimension != right.dimension or left.unit == right.unit:
            continue
        if left.quote_span is not None and left.quote_span == right.quote_span:
            continue
        gap = text[left.end : right.start]
        if len(gap) > 160 or re.search(r"[.;]\s+[А-Я]", gap):
            continue
        equal = abs(left.value - right.value) <= left.tolerance + right.tolerance
        if _EQUIVALENCE.search(gap) and not _DIFFERENCE.search(gap) and not equal:
            prefix = text[max(0, left.start - 60) : left.start]
            suffix = text[right.end : right.end + 100]
            source_statement = re.search(
                r"в таблице|в документе|указано|записано|запись", prefix, re.I
            )
            corrected = re.search(
                r"ошиб|невер|некоррект|корректно|правильно|должн", suffix, re.I
            )
            if not (
                source_statement
                and corrected
                and not re.search(
                    r"корректно|правильно|должн|пересч|соответств|что", gap, re.I
                )
            ):
                return "false_unit_conversion"
        # Вывод о расхождении после двух записей также является арифметическим утверждением.
        conclusion = gap + text[right.end : right.end + 100].split(".")[0]
        if _DIFFERENCE.search(conclusion) and equal:
            return "false_unit_mismatch"
    text = re.sub(r'«[^»]*»|"[^"]*"', "", text)
    for match in _COMPARISON.finditer(text):
        da, fa = _UNITS[match["ua"].casefold()]
        db, fb = _UNITS[match["ub"].casefold()]
        if da != db:
            continue
        a = Decimal(match["a"].replace(",", ".")) * fa
        b = Decimal(match["b"].replace(",", ".")) * fb
        op = match["op"].casefold()
        valid = (
            a <= b
            if op == "не превышает"
            else a < b
            if op in {"меньше", "ниже"}
            else a > b
        )
        if not valid:
            return "false_numeric_comparison"
    for match in _PERIOD.finditer(text):
        # При одинаковом дне месяца длительность не зависит от длины месяцев.
        if match["d1"] == match["d2"] and 1 <= int(match["d1"]) <= 28:
            months = (
                _MONTHS[match["m2"].casefold()] - _MONTHS[match["m1"].casefold()]
            ) % 12
            if Decimal(match["value"].replace(",", ".")) != months:
                return "false_calendar_duration"
    return None


def unsupported_engineering_claim(text: str) -> str | None:
    """Отсекает доказуемую арифметическую ошибку и несовместимые основания сравнения."""
    if reason := false_numeric_claim(text):
        return reason
    quantities = extract_quantities(text)
    if len(quantities) == 2 and not re.search(
        r"ошиб|невер|отсутств|не указан|не обоснов|требует|не совпад|не соответств|расхожд|противореч",
        text,
        re.I,
    ):
        left, right = quantities
        gap = text[left.end : right.start]
        if (
            left.dimension == right.dimension
            and left.unit != right.unit
            and abs(left.value - right.value) <= left.tolerance + right.tolerance
            and (_EQUIVALENCE.search(gap) or gap.strip() == "(")
        ):
            return "equivalent_units"
    if len(re.findall(r"ОСР[-\s]*\d*[-\s]*[ABCАВС]", text, re.I)) > 1 and re.search(
        r"разн\w*\s+значени|противореч|един\w*\s+значени", text, re.I
    ):
        return "incomparable_seismic_map"
    if re.search(r"в PAGE FACTS|scope_location|факты p\d+-f", text, re.I):
        return "extraction_is_not_evidence"
    nominal = re.search(r"\b(?:dn|ду)\s*\d+", text, re.I)
    outer = re.search(r"[ø⌀Ø∅]\s*\d+|наружн\w*\s+диаметр", text, re.I)
    # Не подавляем отдельную претензию о материале, типе изделия или отсутствующем переходе.
    if (
        nominal
        and outer
        and _DIFFERENCE.search(text)
        and not re.search(
            r"отсутств\w*\s+переход|разн\w*\s+материал|паспорт|каталог|несовместим\w*\s+(?:тип|материал)",
            text,
            re.I,
        )
    ):
        return "unverified_diameter_compatibility"
    if re.fullmatch(
        r"\s*(?:Требуется\s+)?(?:проверить|уточнить)\s+(?:расч[её]ты|документацию|проект|всё)\s*[.!]?\s*",
        text,
        re.I,
    ):
        return "non_specific_claim"
    if re.fullmatch(
        r"\s*На листе выявлено несоответствие, требующее проверки инженером[.!]?\s*",
        text,
        re.I,
    ):
        return "non_specific_claim"
    return None
