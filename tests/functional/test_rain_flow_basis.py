# tests/functional/test_rain_flow_basis.py

"""Принятый дождевой сток требует собственных исходных данных, а не подмены мощностью ЛОС."""

from dataclasses import replace

from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
)
from pdrd_analysis_service.domain.engineering_facts import explicit_rain_flow_choices

TEXT = (
    "Расчётный расход дождевых вод с территории площадки склада составляет 42,8 л/с. "
    "Производительность установки очистки составляет 4 л/с."
)


def questions(pages, *, enabled=True):
    """Использует реальный реестр и программную проверку без модельных ответов."""
    report = document_engineering_report(pages, "rain-basis-control", enabled=enabled)
    return [
        f
        for f in program_findings(report)
        if any(
            o.get("check", {}).get("property_key") == "rain_flow_basis"
            for o in f.origin_assertions
        )
    ]


def test_native_rain_flow_creates_a_specific_question_without_asserting_los_shortage():
    """Сохраняет принятое число, территорию и лист; статус остаётся вопросом инженеру."""
    facts = explicit_rain_flow_choices(TEXT)
    assert len(facts) == 1 and facts[0].value_raw == "42,8"
    assert facts[0].identifier == "территории площадки склада"
    result = questions((DocumentPage(8, TEXT, "", ()),))
    assert len(result) == 1
    finding = result[0]
    assert finding.page == 8 and finding.status == "needs_review"
    assert "42,8 л/с" in finding.comment
    assert "площадь водосбора" in finding.comment
    assert "период однократного превышения" in finding.comment
    assert "не доказывает" in finding.recommendation_draft
    assert verified_programmatic_check(finding)
    assert not verified_programmatic_check(replace(finding, status="confirmed"))
    assert not questions((DocumentPage(8, TEXT, "", ()),), enabled=False)


def test_los_capacity_or_unassigned_flow_does_not_become_catchment_design_flow():
    """Не достраивает назначение числа из марки установки или соседней строки."""
    assert not explicit_rain_flow_choices("Производительность ЛОС 4 л/с.")
    assert not explicit_rain_flow_choices("Расчетный расход составляет 42,8 л/с.")


def test_inventory_margin_between_number_and_unit_keeps_literal_evidence():
    """Вертикальное «инв» из рамки не скрывает параметр; исходная цитата не исправляется."""
    text = TEXT.replace("42,8 л/с", "42,8\nинв л/с")
    facts = explicit_rain_flow_choices(text)
    assert len(facts) == 1 and facts[0].unit_raw == "л/с"
    assert "42,8 инв л/с" in facts[0].evidence_text
    assert questions((DocumentPage(8, text, "", ()),))


def test_complete_related_inputs_close_question_but_other_catchment_does_not():
    """Закрывает цепочку только фактическими исходными данными той же территории."""
    accepted = explicit_rain_flow_choices(TEXT)[0]
    inputs = []
    for kind, value, unit in [
        ("catchment_area", "2", "га"),
        ("runoff_coefficient", "0,8", ""),
        ("rainfall_intensity", "27", "л/с/га"),
        ("rainfall_return_period", "5", "лет"),
        ("calculation_reference", "Р-29", ""),
    ]:
        inputs.append(
            replace(
                accepted,
                property_type=kind,
                property_name=kind,
                relation="",
                value_raw=value,
                unit_raw=unit,
                evidence_text=f"{accepted.identifier}: {kind} {value} {unit}",
            )
        )
    text = TEXT + "\n" + "\n".join(f.evidence_text for f in inputs)
    page = DocumentPage(8, text, "", (accepted, *inputs))
    assert not questions((page,))
    other = tuple(
        replace(f, identifier="территории гаража", scope_location="территории гаража")
        for f in inputs
    )
    assert questions((replace(page, facts=(accepted, *other)),))
