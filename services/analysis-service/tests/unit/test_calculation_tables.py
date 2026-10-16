# services/analysis-service/tests/unit/test_calculation_tables.py

"""Регрессии расчётных таблиц: правило итога не следует из одного слова «итого»."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.engineering_program import (
    audit_engineering_candidates,
)
from pdrd_analysis_service.domain.analysis import FindingDraft

from .test_engineering_program import report, table_values


def test_closed_table_without_additive_rule_does_not_prove_wrong_sum():
    """Полнота строк не устанавливает способ формирования итогового показателя."""
    values = table_values(total="3.729")
    values = [
        replace(item, calculation_rule="", calculation_evidence="") for item in values
    ]
    result = report(values)
    assert not any(
        check.rule == "table_sum" and check.status == "confirmed"
        for check in result.checks
    )


@pytest.mark.parametrize("mode", ["maximum", "emergency", "", "initial_filling"])
def test_additive_rule_never_mixes_different_or_unknown_modes(mode):
    """Одно явно другое условие запрещает суммирование вместо подстановки удобного режима."""
    values = table_values(total="3.729")
    values[0] = replace(values[0], scope_operating_mode=mode)
    checks = [item for item in report(values).checks if item.rule == "table_sum"]
    assert checks and all(item.status == "not_evaluable" for item in checks)


@pytest.mark.parametrize("change", ["empty", "unit", "column", "partial", "rule_quote"])
def test_sum_requires_values_units_column_coverage_and_literal_rule(change):
    """Пустота не равна нулю, запись sum не доказывает ни единицу, ни состав суммы."""
    values = table_values(total="3.729")
    if change == "empty":
        values[0] = replace(values[0], value_raw="", evidence_text="Пустая ячейка")
    elif change == "unit":
        values[0] = replace(values[0], unit_raw="м³/ч")
    elif change == "column":
        values[0] = replace(values[0], column_binding="ambiguous")
    elif change == "partial":
        values.pop(0)
    else:
        values[-2] = replace(
            values[-2], calculation_evidence="Отсутствующее в цитате правило"
        )
    assert not any(item.status == "confirmed" for item in report(values).checks)


def test_explicit_sum_rounding_and_independent_modes():
    """Погрешность исходных чисел учитывается; самостоятельный штатный расчёт остаётся проверяемым."""
    values = table_values(total="155.12")
    assert (
        next(item for item in report(values).checks if item.rule == "table_sum").status
        == "no_finding"
    )
    second = [
        replace(item, table_id="Т8", scope_operating_mode="normal")
        for item in table_values(total="3.729")
    ]
    checks = [
        item for item in report([*values, *second]).checks if item.rule == "table_sum"
    ]
    assert {item.status for item in checks} == {"confirmed", "no_finding"}


def test_typed_arithmetic_candidate_without_bound_assertion_cannot_escape_audit():
    """Структурный тип арифметической претензии проверяется и без связи с гипотезой модели."""
    finding = FindingDraft(
        "f",
        1,
        "table",
        "document_consistency",
        "warning",
        "confirmed",
        "Расчёт неверен",
        "3.729",
        "",
        0.9,
        (),
        "",
        (),
        "",
        origin_assertions=({"origin": "vlm", "claim_type": "arithmetic"},),
    )
    (checked,) = audit_engineering_candidates((finding,), report(()))
    assert checked.status == "hypothesis"
    assert (
        replace(
            checked, status=finding.status, origin_assertions=finding.origin_assertions
        )
        == finding
    )


def test_explicit_additive_flow_converts_compatible_units_with_one_mode():
    """Разные единицы допустимы только для одного показателя и одного установленного режима."""
    values = table_values(total="23.6")
    for index in range(6):
        unit = "л/с" if index == 0 else "м³/ч"
        values[index] = replace(
            values[index],
            value_raw=str(index + 1),
            unit_raw=unit,
            column_label="Расход",
            evidence_text=f"S1 Расход {index + 1} {unit}",
        )
    values[-2] = replace(
        values[-2],
        unit_raw="м³/ч",
        column_label="Расход",
        evidence_text="S1 Расход 23.6 м³/ч. Сумма строк",
    )
    check = next(item for item in report(values).checks if item.rule == "table_sum")
    assert check.status == "no_finding" and check.calculation["expected"] == "23.6"


def test_mean_and_maximum_of_same_consumer_are_not_independent_addends():
    """Общий периодический режим не превращает среднее и максимум одного объекта в разных потребителей."""
    values = table_values(total="3.729")
    values[0] = replace(values[0], row_label="Средний расход")
    values[1] = replace(values[1], row_label="Максимальный расход")
    check = next(item for item in report(values).checks if item.rule == "table_sum")
    assert (
        check.status == "not_evaluable"
        and check.reason_code == "alternative_statistics_not_additive"
    )
