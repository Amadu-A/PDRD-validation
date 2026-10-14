# services/analysis-service/src/pdrd_analysis_service/domain/engineering_claims.py

"""Консервативная проверка явных арифметических утверждений модели.

Отбрасываются только доказуемо неверные сравнения совместимых единиц и явные
календарные длительности. Неясные условия и инженерная совместимость не угадываются.
"""

import re
from decimal import Decimal

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


def false_numeric_claim(text: str) -> str | None:
    """Возвращает код доказуемо неверного утверждения либо оставляет его модели."""
    # Цитата ошибочной записи может сама быть доказательством корректного замечания.
    if re.search(r"ошибоч|неверн|некоррект|неправильн", text, re.I):
        return None
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
