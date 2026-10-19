# services/analysis-service/tests/unit/test_requirement_plan.py

"""Независимое обнаружение на синтетическом корпусе; это не оценка recall на ИОС2.2."""

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
from pdrd_analysis_service.domain.requirement_applicability import RequirementEvidence
from pdrd_analysis_service.domain.requirement_plan import (
    build_requirement_plan,
    requirement_checks,
    requirement_coverage,
)

DAY = "2026-10-19"
CODE = "СП 100.13330.2025"


def observed(value="4", *, page=1, **extra):
    """Исходное значение с буквальным классом объекта, без заранее созданного замечания."""
    raw = {
        "kind": "parameter",
        "subject_type": "system",
        "identifier": "ПГ7",
        "property_type": "parameter",
        "property_name": "Расход",
        "value_raw": value,
        "unit_raw": "л/с",
        "evidence_text": f"Пожарный гидрант ПГ7. Расход: {value} л/с.",
        **extra,
    }
    if extra.get("scope_operating_mode"):
        raw["evidence_text"] += " Режим " + extra["scope_operating_mode"] + "."
    return LocatedDocumentFact(page, 1, document_fact_from_mapping(raw))


def requirement(
    text="4.1 Для пожарных гидрантов расход не менее 5 л/с.",
    *,
    kind="N",
    sid=None,
    **extra,
):
    """Фрагмент учебного корпуса; обозначение не является ссылкой на действующий СП."""
    return RequirementEvidence(
        kind, sid or kind + "1", text, 5, CODE, "point-1", **extra
    )


def status(text=None):
    """Проверяемая справочная запись учебного корпуса, не сведения о реальном документе."""
    return requirement(
        text or f"Статус {CODE} на {DAY}: действует; требования обязательны.", sid="N2"
    )


def plan(*, items=None, sources=None, texts=None):
    """Выполняет маршрут напрямую из фактов и цитат, без findings и VLM."""
    items = (observed(),) if items is None else items
    sources = (requirement(), status()) if sources is None else sources
    texts = (
        {item.page: item.fact.evidence_text for item in items}
        if texts is None
        else texts
    )
    return build_requirement_plan(
        items, sources=sources, page_texts=texts, assessed_on=DAY
    )


def target(records):
    """Выбирает собственно положение, исключая справочную строку корпуса."""
    return next(r for r in records if r.source_ids[0] == "N1")


@pytest.mark.parametrize(
    "value,result", [("4", "violated"), ("5", "satisfied"), ("7", "satisfied")]
)
def test_discovers_generic_class_requirement_without_candidate_or_exact_project_tag(
    value, result
):
    """Общее правило для класса создаёт самостоятельную проверку конкретного гидранта."""
    record = target(plan(items=(observed(value),)))
    assert record.result == result and record.applicability == "applicable"
    assert record.currency == "current_mandatory" and record.clause == "4.1"
    assert record.source_ids == ("N1", "N2") and record.currency_evidence
    coverage = requirement_coverage((record,))
    assert coverage["completed"] == 1 and coverage["coverage"] == 1
    assert coverage["new_vlm_calls"] == 0


@pytest.mark.parametrize(
    "proof", [None, "старый", "необязательный", "отменён", "чужой"]
)
def test_unknown_edition_or_obligation_never_produces_normative_finding(proof):
    """Год, similarity и текст ТЗ не подтверждают обязательность и актуальность нормы."""
    sources = [requirement()]
    if proof == "старый":
        sources.append(
            status(f"Статус {CODE} на 2025-10-19: действует; требования обязательны.")
        )
    elif proof == "необязательный":
        sources.append(status(f"Статус {CODE} на {DAY}: действует."))
    elif proof == "отменён":
        sources.append(
            status(f"Статус {CODE} на {DAY}: отменён; требования обязательны.")
        )
    elif proof == "чужой":
        sources.append(replace(status(), kind="T", source_id="T1"))
    record = target(plan(sources=tuple(sources)))
    assert record.result in {"applicability_unknown", "not_applicable"}
    assert all(
        check.status == "not_evaluable" for check in requirement_checks((record,))
    )


@pytest.mark.parametrize(
    "condition,mode,result",
    [
        ("при пожарном режиме", "fire", "violated"),
        ("при пожарном режиме", "normal", "not_applicable"),
        ("при пожарном режиме", "", "applicability_unknown"),
        (
            "при пожарном режиме и температуре ниже нуля",
            "fire",
            "applicability_unknown",
        ),
        ("при высоте здания более 50 м", "fire", "applicability_unknown"),
    ],
)
def test_conditions_are_proved_separately(condition, mode, result):
    """Совпадение одного режима не разрешает остальные условия применимости."""
    record = target(
        plan(
            items=(observed(scope_operating_mode=mode),),
            sources=(
                requirement(
                    f"4.1 Для пожарных гидрантов {condition}, расход не менее 5 л/с."
                ),
                status(),
            ),
        )
    )
    assert record.result == result


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"point_id": ""}, "incomplete_requirement_source"),
        ({"page": None}, "incomplete_requirement_source"),
        (
            {"text": "4.1 Для гидрантов не допускается расход не менее 5 л/с."},
            "unsupported_requirement_modality",
        ),
    ],
)
def test_missing_or_unparsed_source_is_not_converted_to_a_claim(change, reason):
    """Неполная ссылка и отрицание правила остаются диагностикой плана."""
    record = target(plan(sources=(replace(requirement(), **change), status())))
    assert record.reason_code == reason
    assert requirement_checks((record,))[0].status == "not_evaluable"


def test_no_source_and_no_object_do_not_invent_findings():
    """Отсутствие корпуса и пропуск факта не становятся недостатками проекта."""
    assert plan(sources=()) == ()
    record = target(plan(items=()))
    assert record.result == "insufficient_data" and not requirement_checks((record,))
    assert (
        target(plan(texts={1: "Другое значение"})).reason_code
        == "project_evidence_not_bound"
    )


def test_class_boundary_does_not_apply_reservoir_limit_to_hydrant():
    """Общее соседство в chunk не переносит норму на другой класс объекта."""
    record = target(
        plan(
            sources=(
                requirement(
                    "4.1 Для гидрантов требуется паспорт. Для резервуаров расход не менее 5 л/с."
                ),
                status(),
            )
        )
    )
    assert record.result == "insufficient_data"


def test_recommendation_and_exception_are_not_mandatory_violations():
    """Рекомендация отделена от обязательного требования, исключение требует данных."""
    r = requirement("4.1 Для гидрантов рекомендуется расход не менее 5 л/с.")
    assert target(plan(sources=(r,))).result == "recommendation"
    r = requirement("4.1 Для гидрантов расход не менее 5 л/с, кроме временных сетей.")
    assert target(plan(sources=(r, status()))).result == "applicability_unknown"


@pytest.mark.parametrize("kind", ["T", "U"])
def test_conflict_keeps_all_normative_and_customer_requirements(kind):
    """N не ослабляется T/U; два независимых нормативных пункта сохраняются в конфликте."""
    sources = (
        requirement(
            "4.1 Для гидрантов расход не менее 5 л/с.\n4.2 Для гидрантов расход не менее 6 л/с."
        ),
        status(),
        requirement("Для гидрантов расход не более 3 л/с.", kind=kind),
    )
    records = plan(sources=sources)
    customer = next(r for r in records if r.source_kind == kind)
    assert customer.result == "source_conflict"
    checks = requirement_checks(records)
    conflicts = [c for c in checks if c.rule == "requirement_plan_conflict"]
    assert len(conflicts) == 2 and len({c.check_id for c in conflicts}) == 2
    assert all(set(c.source_ids) == {"N1", "N2", kind + "1"} for c in conflicts)
    assert requirement_coverage(records)["outcomes"]["source_conflict"] == 1


def test_two_clauses_of_one_source_never_share_finding_identity():
    """Разные требования к одному свойству не теряются при объединении результатов."""
    r = requirement(
        "4.1 Для гидрантов расход не менее 5 л/с.\n4.2 Для гидрантов расход не менее 6 л/с."
    )
    checks = [
        c
        for c in requirement_checks(plan(sources=(r, status())))
        if c.status == "confirmed"
    ]
    assert len(checks) == 2 and len({c.check_id for c in checks}) == 2


def test_calculation_is_only_specific_question_with_known_parameter_and_explicit_requirement():
    """Вопрос ограничен проверенными страницами; глобальное отсутствие расчёта не утверждается."""
    r = requirement("4.1 Для гидрантов расход должен быть обоснован расчётом.")
    record = target(plan(sources=(r, status())))
    assert record.result == "question" and record.comparison["checked_pages"] == [1]
    assert not any(c.status == "confirmed" for c in requirement_checks((record,)))


def test_conflicting_project_observations_require_resolution():
    """Разные значения одного режима не позволяют выбрать удобную сторону нормативного сравнения."""
    record = target(plan(items=(observed(), observed("6", page=2))))
    assert record.result == "insufficient_data" and len(record.observations) == 2


@pytest.mark.parametrize(
    "text",
    [
        "4.1 Для подземных гидрантов расход не менее 5 л/с.",
        "4.1 Для гидрантов здания высотой более 50 м расход не менее 5 л/с.",
        "4.1 Для гидрантов и резервуаров расход не менее 5 л/с.",
    ],
)
def test_object_class_does_not_prove_additional_qualifiers(text):
    """Класс из цитаты проекта не доказывает размещение, высоту здания и смешанную область."""
    record = target(plan(sources=(requirement(text), status())))
    assert record.result in {"applicability_unknown", "insufficient_data"}


def test_source_code_cannot_be_taken_from_cross_reference_and_conflicting_status_is_unknown():
    """Нельзя назначить источнику редакцию упомянутого внутри другого документа."""
    raw = replace(
        requirement(),
        source_file="Неидентифицированный документ",
        text=f"4.1 Для гидрантов расход не менее 5 л/с. Ссылка на {CODE}.",
    )
    assert target(plan(sources=(raw, status()))).currency == "unknown"
    cancelled = replace(status(f"Статус {CODE} на {DAY}: отменён."), source_id="N3")
    record = target(plan(sources=(requirement(), status(), cancelled)))
    assert (
        record.currency == "conflicting_status"
        and record.result == "applicability_unknown"
    )


@pytest.mark.parametrize(
    "text",
    [
        f"Статус СП 200.13330.2025 на {DAY}: действует; требования обязательны (ссылка на {CODE}).",
        f"Статус СП 200.13330.2025 на {DAY}: действует; требования обязательны. Статус {CODE} на {DAY}: действует.",
    ],
)
def test_status_and_binding_are_attached_to_same_edition_in_same_statement(text):
    """Соседнее обозначение и обязательность другого СП не подтверждают текущий документ."""
    record = target(plan(sources=(requirement(), status(text))))
    assert record.currency in {"unknown", "current_binding_unknown"}
    assert record.result == "applicability_unknown"


def test_program_replays_proof_with_actual_sources_and_rejects_forged_text():
    """Финализация повторяет проверку; один флаг confirmed ничего не доказывает."""
    from pdrd_analysis_service.domain.analysis import NormativeSource

    item = observed()
    sources = (requirement(), status())
    texts = {1: item.fact.evidence_text}
    registry = build_decision_registry(
        document_id="synthetic", facts=(item,), page_texts=texts
    )
    report = run_engineering_program(
        registry,
        page_texts=texts,
        requirement_sources=sources,
        requirement_plan_enabled=True,
        assessed_on=DAY,
    )
    ns = tuple(
        NormativeSource(
            s.source_id, s.point_id, 0.9, s.source_file, None, s.page, 0, s.text
        )
        for s in sources
    )
    findings = program_findings(report, normative=ns)
    assert len(findings) == 1 and findings[0].status == "confirmed"
    assert verified_programmatic_check(findings[0])
    assert not verified_programmatic_check(
        replace(findings[0], comment="Неподтверждённое нарушение")
    )
    assert not verified_programmatic_check(replace(findings[0], basis_sources=ns[:1]))
