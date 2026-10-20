# services/analysis-service/tests/unit/test_quality_resilience.py

"""Сотрудничество прежних проверок и N-first, предметные вопросы без готовых VLM-замечаний."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    run_engineering_program,
    verified_programmatic_check,
)
from pdrd_analysis_service.domain.design_decisions import build_decision_registry
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_checks import literal_fact_value
from pdrd_analysis_service.domain.engineering_facts import explicit_protection_choices
from pdrd_analysis_service.domain.requirement_applicability import RequirementEvidence
from pdrd_analysis_service.domain.requirement_plan import build_requirement_plan

from .test_requirement_plan import CODE, DAY, observed, requirement, status


@pytest.mark.parametrize("kind", ["T", "U", "N"])
def test_new_plan_preserves_legacy_power_constraint(kind):
    """Неподдержанная новым парсером мощность продолжает проверяться прежним правилом."""
    quote = "Н1 Мощность 5 кВт"
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "identifier": "Н1",
            "property_name": "Мощность",
            "value_raw": "5",
            "unit_raw": "кВт",
            "evidence_text": quote,
            "extraction_method": "pdf_text",
        }
    )
    item = LocatedDocumentFact(1, 1, fact)
    source = RequirementEvidence(
        kind, kind + "1", "4.1 Н1 Мощность не менее 8 кВт.", 5, CODE, "point-power"
    )
    sources = (source, status()) if kind == "N" else (source,)
    texts = {1: quote}
    registry = build_decision_registry(
        document_id="job", facts=(item,), page_texts=texts
    )
    report = run_engineering_program(
        registry,
        page_texts=texts,
        requirement_sources=sources,
        requirement_plan_enabled=True,
        assessed_on=DAY,
    )
    checks = [
        check for check in report.checks if check.rule == "requirement_applicability"
    ]
    assert len(checks) == 1 and checks[0].status == "confirmed"
    assert checks[0].calculation["source_kind"] == kind
    assert source.source_id in checks[0].source_ids


@pytest.mark.parametrize(
    "constraint", ["расход не менее", "РАСХОД НЕ МЕНЕЕ", "расход >="]
)
def test_legacy_and_new_same_constraint_do_not_create_duplicate_findings(constraint):
    """Два маршрута сохраняют одно и то же доказанное нарушение без второй карточки."""
    item = observed()
    text = f"4.1 ПГ7 {constraint} 5 л/с."
    sources = (requirement(text), status())
    texts = {1: item.fact.evidence_text}
    registry = build_decision_registry(
        document_id="job", facts=(item,), page_texts=texts
    )
    report = run_engineering_program(
        registry,
        page_texts=texts,
        requirement_sources=sources,
        requirement_plan_enabled=True,
        assessed_on=DAY,
    )
    assert sum(check.status == "confirmed" for check in report.checks) == 1
    assert report.requirement_plan["legacy_checks"]["covered_by_requirement_plan"] == 1


def test_unknown_normative_currency_cannot_reappear_through_legacy_route():
    """Восстановление старого обхода не обходит проверку нормативных оснований."""
    item = observed()
    texts = {1: item.fact.evidence_text}
    registry = build_decision_registry(
        document_id="job", facts=(item,), page_texts=texts
    )
    report = run_engineering_program(
        registry,
        page_texts=texts,
        requirement_sources=(requirement("4.1 ПГ7 расход не менее 5 л/с."),),
        requirement_plan_enabled=True,
        assessed_on=DAY,
    )
    assert not any(check.status == "confirmed" for check in report.checks)


@pytest.mark.parametrize(
    "quote,dependency",
    [
        (
            "Проектом предусматривается установка трех резервуаров противопожарного запаса воды, "
            "объемом 100 м3, 100 м3, 50 м3.",
            "fire_storage_basis",
        ),
        (
            "Для целей наружного и внутреннего пожаротушения склада материалов "
            "предусмотрен противопожарный водопровод В2.",
            "protected_object_basis",
        ),
    ],
)
def test_adopted_protection_choice_yields_specific_question_without_numeric_or_normative_claim(
    quote, dependency
):
    """Состав запаса не подменяется суммой полезного объёма; выбор защиты требует предметных оснований."""
    facts = explicit_protection_choices(quote)
    assert facts
    items = tuple(LocatedDocumentFact(1, i, fact) for i, fact in enumerate(facts, 1))
    registry = build_decision_registry(
        document_id="job", facts=items, page_texts={1: quote}
    )
    report = run_engineering_program(registry, page_texts={1: quote})
    findings = program_findings(report)
    selected = [check for check in report.checks if check.property_key == dependency]
    assert len(selected) == 1 and selected[0].status == "needs_review"
    assert findings and all(
        finding.status == "needs_review" and not finding.basis_sources
        for finding in findings
    )
    assert all(verified_programmatic_check(finding) for finding in findings)


def test_title_is_not_misread_as_an_adopted_protection_solution():
    """Заголовок котельной со складом не создаёт вопрос об отсутствующей защите."""
    assert (
        explicit_protection_choices(
            "Котельная со складом материалов. Содержание тома. 100 м3, 100 м3, 50 м3."
        )
        == ()
    )


def test_protection_question_uses_existing_reference_and_input_fact_types():
    """Связанные исходные данные и ссылки не требуют фиктивных новых ролей для снятия вопроса о полноте."""
    quote = "Для целей наружного пожаротушения склада материалов предусмотрен водопровод В2."
    accepted = explicit_protection_choices(quote)[0]
    facts = [accepted]
    for kind, value in (
        ("design_input", "3"),
        ("requirement_reference", "СП 100.13330.2025"),
        ("calculation_reference", "Р1"),
        ("document_reference", "Схема 1"),
    ):
        facts.append(
            replace(
                accepted,
                property_type=kind,
                property_name="Основание решения",
                relation="",
                value_raw=value,
                evidence_text=f"{accepted.identifier}: основание решения {value}.",
            )
        )
    texts = {1: "\n".join(fact.evidence_text for fact in facts)}
    registry = build_decision_registry(
        document_id="job",
        page_texts=texts,
        facts=tuple(LocatedDocumentFact(1, i, fact) for i, fact in enumerate(facts, 1)),
    )
    report = run_engineering_program(registry, page_texts=texts)
    assert not any(
        check.property_key == "protected_object_basis" for check in report.checks
    )
    assert len(report.registry.evidence) == len(facts)


def test_question_proof_cannot_substitute_adopted_solution_in_its_own_metadata():
    """Даже согласованная подмена комментария и метаданных не заменяет проверенное проектное решение."""
    quote = "Для целей наружного пожаротушения склада материалов предусмотрен водопровод В2."
    fact = explicit_protection_choices(quote)[0]
    registry = build_decision_registry(
        document_id="job",
        facts=(LocatedDocumentFact(1, 1, fact),),
        page_texts={1: quote},
    )
    original = program_findings(
        run_engineering_program(registry, page_texts={1: quote})
    )[0]
    origin = next(
        part
        for part in original.origin_assertions
        if part.get("origin") == "programmatic_engineering_check"
    )
    old = origin["check"]["calculation"]["accepted"]
    forged = replace(
        original,
        comment=original.comment.replace(old, "другое решение "),
        origin_assertions=tuple(
            {
                **part,
                "check": {
                    **part["check"],
                    "calculation": {
                        **part["check"]["calculation"],
                        "accepted": "другое решение ",
                    },
                },
            }
            if part is origin
            else part
            for part in original.origin_assertions
        ),
    )
    assert verified_programmatic_check(original)
    assert not verified_programmatic_check(forged)


@pytest.mark.parametrize(
    "value,quote,valid",
    [
        ("3", "S1 Параметр 3.", True),
        ("3", "S1 Параметр 3.1.", False),
        ("3", "S1 Параметр 13.", False),
        ("Р1", "S1 Расчёт Р1.", True),
    ],
)
def test_sentence_period_is_not_a_decimal_continuation(value, quote, valid):
    """Конечная точка не теряет факт, а часть чужого числа не становится самостоятельным значением."""
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "subject_type": "system",
            "identifier": "S1",
            "property_name": "Параметр",
            "value_raw": value,
            "evidence_text": quote,
            "extraction_method": "pdf_text",
        }
    )
    item = LocatedDocumentFact(1, 1, fact)
    assert literal_fact_value(item) is valid
    registry = build_decision_registry(
        document_id="job", facts=(item,), page_texts={1: quote}
    )
    assert (registry.evidence[0].grounding == "literal_text") is valid


def test_fuel_pumps_are_not_applied_to_water_system_by_boiler_word_match():
    """Фрагмент смежной дисциплины сохраняется в плане с причиной непроверенности."""
    item = replace(
        observed(),
        fact=replace(
            observed().fact, evidence_text="Г1 водоснабжение котельной расход 4 л/с"
        ),
    )
    source = requirement(
        "4.1 Должно предусматриваться дистанционное отключение насосов жидкого топлива."
    )
    records = build_requirement_plan(
        (item,),
        page_texts={1: item.fact.evidence_text},
        sources=(source, status()),
        assessed_on=DAY,
    )
    record = next(record for record in records if record.source_ids[0] == "N1")
    assert (
        record.reason_code == "different_physical_system"
        and record.result != "violated"
    )


def test_valid_cross_discipline_class_requirement_is_not_filtered_by_source_title():
    """Название котельного СП не запрещает релевантное правило для пожарного гидранта."""
    item = observed()
    source = replace(requirement(), source_file=CODE + " Котельные установки.pdf")
    records = build_requirement_plan(
        (item,),
        page_texts={1: item.fact.evidence_text},
        sources=(source, status()),
        assessed_on=DAY,
    )
    assert any(record.result == "violated" for record in records)


@pytest.mark.parametrize("kind", ["T", "U"])
def test_legacy_customer_check_does_not_override_new_plan_source_conflict(kind):
    """Возврат старого прохода не подтверждает требование заказчика, конфликтующее с N."""
    item = observed()
    texts = {1: item.fact.evidence_text}
    sources = (
        requirement("4.1 ПГ7 расход не менее 5 л/с."),
        status(),
        requirement("4.1 ПГ7 расход не более 3 л/с.", kind=kind),
    )
    registry = build_decision_registry(
        document_id="job", facts=(item,), page_texts=texts
    )
    report = run_engineering_program(
        registry,
        page_texts=texts,
        requirement_sources=sources,
        requirement_plan_enabled=True,
        assessed_on=DAY,
    )
    assert (
        sum(check.rule == "requirement_plan_conflict" for check in report.checks) == 1
    )
    assert not any(
        check.rule == "requirement_applicability"
        and check.status == "confirmed"
        and (check.calculation or {}).get("source_kind") == kind
        for check in report.checks
    )


def test_current_edition_does_not_prove_unknown_physical_subject_qualifiers():
    """Точный тег и статус редакции не превращают котельный насос в пожарный гидрант."""
    item = observed()
    texts = {1: item.fact.evidence_text}
    registry = build_decision_registry(
        document_id="job", facts=(item,), page_texts=texts
    )
    report = run_engineering_program(
        registry,
        page_texts=texts,
        requirement_sources=(
            requirement("4.1 Для котельных насосов ПГ7 расход не менее 5 л/с."),
            status(),
        ),
        requirement_plan_enabled=True,
        assessed_on=DAY,
    )
    assert not any(check.status == "confirmed" for check in report.checks)
