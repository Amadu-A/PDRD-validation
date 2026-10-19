# tests/functional/test_quality_requirements.py

"""Регрессии этапа 5 и точное объединение нормативного и свободного обнаружения."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pdrd_analysis_service.application.evidence_audit import evidence_audit
from pdrd_analysis_service.application.use_cases.document_context import (
    merge_cross_page_findings,
)
from pdrd_analysis_service.application.use_cases.finalization import (
    _safe_generated_text,
)
from pdrd_analysis_service.domain.engineering_assertion import assertion_from_facts

from tests.functional.test_engineering_evidence_gate import (
    audit,
    candidate,
    fact,
    finalize,
)


def test_actual_corpus_calculation_clauses_are_planned_without_claiming_current_edition():
    """Настоящие формулировки СП становятся правилами плана без выдуманной актуальности."""
    from pdrd_analysis_service.domain.requirement_applicability import (
        RequirementEvidence,
    )
    from pdrd_analysis_service.domain.requirement_plan import (
        build_requirement_plan,
        requirement_checks,
    )

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "fixtures/engineering/stage5-normative-excerpts.json"
        ).read_text("utf-8")
    )
    items = (
        fact(
            "5",
            prop="Количество",
            unit="шт",
            identifier="ПГ7",
            evidence_text="Гидрант ПГ7 количество 5 шт",
        ),
        fact(
            "110",
            prop="Диаметр",
            unit="мм",
            evidence_text="Трубопровод S1 диаметр 110 мм",
        ),
    )
    texts = {1: "\n".join(item.fact.evidence_text for item in items)}
    for excerpt in fixture["excerpts"]:
        if excerpt["clause"] not in {"8.10", "11.44"}:
            continue
        source = RequirementEvidence(
            "N",
            "N1",
            excerpt["text"],
            excerpt["page"],
            excerpt["file"],
            excerpt["sha256"],
        )
        records = build_requirement_plan(
            items, page_texts=texts, sources=(source,), assessed_on="2026-10-19"
        )
        assert (
            records[0].rule == "required_calculation"
            and records[0].currency == "unknown"
        )
        assert not any(
            check.status in {"confirmed", "needs_review"}
            for check in requirement_checks(records)
        )


@pytest.mark.parametrize(
    "comment,reason",
    [
        (
            "Пропущен раздел 11: между разделами 10 и 12 нет пункта.",
            "section_composition_requirement_not_established",
        ),
        (
            "Норматив может не покрывать требование к гидранту.",
            "normative_applicability_claim_not_established",
        ),
    ],
)
async def test_unproven_structural_or_normative_claim_keeps_full_diagnostic(
    comment, reason
):
    """G2/G5: формальная гипотеза не становится фактическим замечанием."""
    a = fact("10", prop="Расход", unit="л/с")
    original = replace(candidate(a), comment=comment)
    guarded = audit(original, a)
    _, final, metrics = await finalize(guarded)
    assert not final and metrics["vlm_call_count"] == 0
    assert evidence_audit(guarded)["reason_code"] == reason
    assert evidence_audit(guarded)["original"]["comment"] == comment


async def test_calculation_question_is_bounded_to_checked_materials():
    """G6: категоричное отсутствие расчёта во всём проекте заменено ограниченным вопросом."""
    a = fact("10", prop="Расход наружного пожаротушения", unit="л/с")
    original = replace(
        candidate(a, claim_type="missing_justification"),
        comment="В проекте отсутствует расчёт расхода S1 10 л/с.",
    )
    guarded = audit(original, a)
    _, final, _ = await finalize(guarded)
    assert len(final) == 1 and final[0].status == "needs_review"
    assert "в предоставленной части" in final[0].comment
    saved = next(
        o
        for o in guarded.origin_assertions
        if o.get("origin") == "checked_scope_wording"
    )
    assert (
        saved["checked_pages"] == [1]
        and saved["original"]["comment"] == original.comment
    )


async def test_unidentified_project_wide_absence_is_diagnostic_instead_of_invented_question():
    """Отсутствие связи с конкретным решением не исправляется выдумыванием вопроса."""
    a = fact("10", prop="Расход", unit="л/с")
    original = replace(
        candidate(a),
        origin_assertions=(),
        comment="В проекте отсутствует расчёт гидравлики.",
    )
    guarded = audit(original, a)
    _, final, metrics = await finalize(guarded)
    assert not final and metrics["vlm_call_count"] == 0
    assert (
        evidence_audit(guarded)["reason_code"]
        == "project_wide_calculation_absence_not_established"
    )


@pytest.mark.parametrize("ending", ["Ø110x…", "Ø110x10 и", "Ø110*10,", "«Ду100"])
def test_unfinished_editor_text_does_not_replace_complete_claim(ending):
    """G4: редактор не заменяет полноценную исходную формулировку оборванным ответом."""
    fallback = (
        "Сопоставить обозначения Ø110x10 и Ду100 с учётом разных определений диаметра."
    )
    assert (
        _safe_generated_text(
            generated="Проверить " + ending,
            fallback=fallback,
            generic="Проверить запись.",
        )
        == fallback
    )


def test_routes_merge_only_same_exact_requirement_with_all_history_and_locations():
    """Два направления не усиливают доказанность; другие требования остаются независимыми."""
    from pdrd_analysis_service.application.engineering_program import (
        program_findings,
        run_engineering_program,
    )
    from pdrd_analysis_service.domain.design_decisions import build_decision_registry
    from pdrd_analysis_service.domain.requirement_applicability import (
        RequirementEvidence,
    )

    a = fact("4", prop="Расход", unit="л/с", evidence_text="Гидрант S1 расход 4 л/с")
    quote = "Для гидрантов расход не менее 5 л/с."
    raw = RequirementEvidence("T", "T1", quote, 1, "ТЗ", "r-1")
    registry = build_decision_registry(
        document_id="test", facts=(a,), page_texts={1: a.fact.evidence_text}
    )
    report = run_engineering_program(
        registry,
        page_texts={1: a.fact.evidence_text},
        requirement_sources=(raw,),
        requirement_plan_enabled=True,
        assessed_on="2026-10-19",
    )
    from pdrd_analysis_service.domain.analysis import TechnicalAssignmentSource

    source = TechnicalAssignmentSource(
        source_id="T1",
        point_id="r-1",
        score=1,
        text=quote,
        page=1,
        technical_assignment_id=None,
        analysis_document_id=None,
        section_id=None,
        source_sha256=None,
        source_file="ТЗ",
    )
    program = program_findings(report, technical=(source,))[0]
    free = replace(
        candidate(a, finding_id="free-1"),
        comment=quote,
        technical_assignment_source_ids=("T1",),
        technical_assignment_basis_sources=(source,),
        evidence_locations=({"page": 2, "text": "Дополнительная область"},),
    )
    separate = replace(
        free,
        finding_id="free-2",
        comment="Другое правило к тому же гидранту.",
        technical_assignment_source_ids=("T2",),
        technical_assignment_basis_sources=(
            replace(source, source_id="T2", text="Другое правило"),
        ),
        origin_assertions=(
            assertion_from_facts((a,), claim_type="missing_justification").as_origin(),
        ),
    )
    merged = merge_cross_page_findings(
        page_findings={1: (free, separate, program)}, cross_findings=()
    )
    result = merged[1]
    assert len(result) == 2
    kept = next(f for f in result if f.finding_id == program.finding_id)
    assert kept.status == program.status and kept.comment == program.comment
    assert {loc["page"] for loc in kept.evidence_locations} == {1, 2}
    assert any(
        o.get("finding_id") == "free-1"
        and o.get("reason_code") == "same_verified_requirement_claim"
        for o in kept.origin_assertions
    )


def test_system_purpose_aliases_keep_two_human_claims_as_one_identity():
    """G3: названия свойства мигрируют в единое назначение В2, не смешивая иные претензии."""
    a = fact(
        "водоотведение",
        prop="Наименование сети",
        unit="",
        identifier="В2",
        property_type="relationship",
    )
    b = fact(
        "противопожарное водоснабжение",
        page=2,
        prop="Назначение системы",
        unit="",
        identifier="В2",
        property_type="relationship",
    )
    first = audit(candidate(a, b, finding_id="first"), a, b)
    alias = replace(a, fact=replace(a.fact, property_name="Назначение"))
    second = audit(candidate(alias, b, finding_id="second"), alias, b)
    merged = merge_cross_page_findings(
        page_findings={1: (first, second)}, cross_findings=()
    )
    assert sum(map(len, merged.values())) == 1
