# tests/functional/test_quality_completeness.py

"""Сырые страницы → программная проверка → действующая финализация без готовых замечаний."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pdrd_analysis_service.application.engineering_program import (
    audit_engineering_candidates,
    program_findings,
)
from pdrd_analysis_service.application.evidence_audit import (
    audit_findings_evidence,
    evidence_audit,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
    merge_cross_page_findings,
)
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
)
from pdrd_analysis_service.domain.engineering_facts import merge_text_engineering_facts
from pdrd_analysis_service.domain.structural_evidence import structural_counterevidence

from tests.functional.test_calculation_table_review import candidate as table_candidate
from tests.functional.test_calculation_table_review import real_report
from tests.functional.test_engineering_evidence_gate import (
    UnavailableVision,
    audit,
    candidate,
    fact,
    finalize,
)


async def test_three_units_of_one_filling_rule_merge_with_all_values_and_candidate_ids():
    """Один вопрос о правиле итога содержит три показателя, а эксплуатационный итог остаётся отдельно."""
    report = real_report()
    drafts = audit_engineering_candidates(
        tuple(
            table_candidate(value, unit)
            for value, unit in (
                ("116,033", "м³/сут"),
                ("4,835", "м³/ч"),
                ("1,343", "л/с"),
                ("155,122", "м³/сут"),
            )
        ),
        report,
    )
    merged = merge_cross_page_findings(page_findings={30: drafts}, cross_findings=())
    _, final, metrics = await finalize(*merged[30])
    assert len(final) == 2 and metrics["vlm_call_count"] == 0
    filling = next(item for item in final if "116,033" in item.comment)
    assert all(value in filling.comment for value in ("116,033", "4,835", "1,343"))
    assert (
        len(
            [
                origin
                for origin in filling.origin_assertions
                if origin.get("origin") == "merged_finding"
            ]
        )
        == 2
    )


def source_pages(transform=lambda text: text):
    """Передаёт полный текст физических страниц; эталонные замечания отсутствуют во входе."""
    data = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/engineering/stage4-source-excerpts.json"
        ).read_text("utf-8")
    )
    return tuple(
        DocumentPage(
            item["page"],
            text := transform(item["text"]),
            "",
            merge_text_engineering_facts((), text, item["page"]),
        )
        for item in data["pages"]
    )


@pytest.mark.parametrize("other_kind", ["наружное", "внутреннее"])
def test_fire_demand_does_not_invent_identity_of_protected_objects(other_kind):
    """Общее назначение расхода не приравнивает разные защищаемые объекты или виды пожаротушения."""
    texts = (
        "Расчетный расход на наружное пожаротушение принят 10 л/с.",
        f"Расчетный расход на {other_kind} пожаротушение принят 5 л/с.",
    )
    pages = tuple(
        DocumentPage(n, text, "", merge_text_engineering_facts((), text, n))
        for n, text in enumerate(texts, 1)
    )
    located = tuple(
        LocatedDocumentFact(p.page, i, f)
        for p in pages
        for i, f in enumerate(p.facts, 1)
    )
    assert len(located) == 2
    assert discover_conflicts(located, max_candidates=20) == ()
    report = document_engineering_report(pages, "different-fire-demands")
    questions = program_findings(report)
    assert len(questions) == 2 and all(f.status == "needs_review" for f in questions)


@pytest.mark.parametrize(
    "old,new", [("таблице 2", "таблице 2"), ("таблице 2", "таблице 3")]
)
async def test_reference_discovery_from_real_page_without_model_or_canned_finding(
    old, new
):
    """Реальная ошибка ссылки обнаруживается; исправление исходного адреса убирает ровно её."""
    report = document_engineering_report(
        source_pages(lambda text: text.replace(old, new)), "reference-proof"
    )
    drafts = tuple(
        f
        for f in program_findings(report)
        if any(
            o.get("check", {}).get("rule") == "topic_reference_consistency"
            for o in f.origin_assertions
        )
    )
    _, final, metrics = await finalize(*drafts)
    assert len(final) == (1 if old == new else 0)
    assert metrics["vlm_call_count"] == 0
    if final:
        assert "таблица 2" in final[0].comment and "таблица 3" in final[0].comment
        assert all(location["page"] == 20 for location in final[0].evidence_locations)


async def test_real_adopted_decisions_produce_concrete_questions_and_keep_sources():
    """Расход, труба и защита двух разных объектов сохраняют отдельные проверяемые вопросы."""
    report = document_engineering_report(source_pages(), "dependency-proof")
    drafts = tuple(
        f
        for f in program_findings(report)
        if any(
            o.get("check", {}).get("rule") == "design_basis_dependency"
            for o in f.origin_assertions
        )
    )
    _, final, metrics = await finalize(*drafts)
    assert len(final) == 4 and metrics["vlm_call_count"] == 0
    assert all(
        f.status == "needs_review" and f.document_context_basis_sources for f in final
    )
    assert any(
        "10 л/с" in f.comment and "нормативное обоснование" in f.comment for f in final
    )
    assert any(
        "110 мм" in f.comment
        and "расчётный расход" in f.comment
        and "потери" in f.comment
        for f in final
    )
    assert not any("Сверить параметр" in f.comment for f in final)
    assert {"здания котельной", "склада угля"} <= {
        origin["check"]["object_key"].split(":", 1)[-1]
        for finding in final
        for origin in finding.origin_assertions
        if origin.get("check", {}).get("property_key") == "protected_object_basis"
    }


async def test_single_scalar_without_claim_stays_lossless_diagnostic():
    """Число без второй стороны не превращается в пользовательское замечание."""
    item = fact("10", prop="Расход воды", unit="л/с")
    original = candidate(item)
    _, final, metrics = await finalize(audit(original, item))
    assert not final and metrics["vlm_call_count"] == 0
    record = metrics["rejected_findings"][0]
    assert record["candidate"]["finding_id"] == original.finding_id
    assert record["reason_code"] == "second_comparison_value_unavailable"


async def test_generic_missing_justification_is_not_a_specific_question():
    """Слова «обоснование параметра» сами по себе не задают инженерную зависимость."""
    item = fact("10", prop="Расход воды", unit="л/с")
    original = replace(
        candidate(item, claim_type="missing_justification"),
        comment="Уточнить обоснование параметра S1 10 л/с.",
    )
    _, final, metrics = await finalize(audit(original, item))
    assert not final
    assert (
        metrics["rejected_findings"][0]["reason_code"]
        == "engineering_question_basis_not_established"
    )


async def test_concrete_original_question_keeps_its_meaning_through_repeated_audit():
    """Конкретный вопрос инженеру сохраняет исходные формулировку, условия и доказательства."""
    item = fact("10", prop="Расход воды", unit="л/с")
    text = (
        "Для S1 принят расход 10 л/с. Каковы исходные условия "
        "и применимое нормативное обоснование пожарного расхода?"
    )
    original = replace(
        candidate(item, claim_type="missing_justification"), comment=text
    )
    _, final, metrics = await finalize(audit(original, item))
    assert final[0].comment == text and final[0].status == "needs_review"
    assert metrics["vlm_call_count"] == 0


async def test_supported_purpose_subsumes_unilateral_diagnostic_without_losing_history():
    """Полное противоречие поглощает его односторонний кандидат, сохраняя оба исходных ID."""
    a = fact("водоснабжение", prop="Назначение системы", unit="")
    b = fact("водоотведение", 2, prop="Назначение системы", unit="")
    partial = audit(candidate(b, finding_id="one-sided"), b)
    full = audit(candidate(a, b, finding_id="complete"), a, b)
    assert evidence_audit(partial)["disposition"] == "diagnostic"
    merged = merge_cross_page_findings(
        page_findings={2: (partial,)}, cross_findings=(full,)
    )
    combined = tuple(f for items in merged.values() for f in items)
    assert len(combined) == 1
    _, final, _ = await finalize(*combined, model=UnavailableVision())
    assert len(final) == 1
    assert any(o.get("finding_id") == "one-sided" for o in final[0].origin_assertions)
    merge = next(
        o for o in final[0].origin_assertions if o.get("origin") == "merged_finding"
    )
    assert merge["reason_code"] == "subsumed_by_supported_comparison"
    assert (
        merge["merged_into"] == "complete"
        and merge["stage"] == "document_deduplication"
    )


@pytest.mark.parametrize(
    "comment,reason",
    [
        ("Расхождение обозначения ИОС2 и ИОС2.2.2", "section_and_subsection_hierarchy"),
        ("Отсутствует пункт «и»", "claimed_missing_section_present_in_source"),
    ],
)
async def test_structure_counterevidence_uses_read_source_not_incomplete_facts(
    comment, reason
):
    """Полный прочитанный заголовок важнее неполного списка строк Understanding."""
    item = fact("10")
    original = replace(candidate(item), comment=comment, origin_assertions=())
    guarded = audit_findings_evidence(
        (original,),
        page_texts={1: "Раздел ИОС2. Часть ИОС2.2.2. и) Перечень мероприятий"},
        stage="structure",
    )[0]
    _, final, metrics = await finalize(guarded)
    assert not final and metrics["rejected_findings"][0]["reason_code"] == reason


def test_hierarchy_guard_keeps_real_different_codes_and_missing_sections():
    """Разные ветви шифра и фактически не прочитанный пункт не объявляются контрдоказательством."""
    assert (
        structural_counterevidence(
            "Расхождение ИОС2.2 и ИОС2.3", "", {1: "ИОС2.2 ИОС2.3"}
        )
        is None
    )


@pytest.mark.parametrize("page_numbers,expected", [((1, 1), 1), ((1, 2), 0)])
async def test_reference_sentence_punctuation_and_local_numbering_are_respected(
    page_numbers, expected
):
    """Точка после номера допустима; одинаковый общий заголовок разных частей не доказывает ошибку."""
    texts = (
        "Ведомость труб представлена в таблице 4.",
        "Ведомость труб представлена в таблице 7.",
    )
    combined = {}
    for page, text in zip(page_numbers, texts, strict=True):
        combined[page] = combined.get(page, "") + text + " "
    combined[3] = "Таблица 4\nТрубы первой группы.\nТаблица 7\nТрубы второй группы."
    report = document_engineering_report(
        tuple(DocumentPage(page, text, "", ()) for page, text in combined.items()),
        "local-references",
    )
    _, final, _ = await finalize(*program_findings(report))
    assert len(final) == expected


def test_toc_continuation_is_checked_separately_from_body_heading():
    """Продолжение оглавления учитывается, но заголовок в теле не заменяет запись оглавления."""
    claim = "В оглавлении отсутствует пункт «и»"
    assert (
        structural_counterevidence(
            claim,
            "",
            {1: "Содержание а) Введение ... 3", 2: "и) Качество воды ............ 17"},
        )
        == "claimed_missing_section_present_in_source"
    )
    assert (
        structural_counterevidence(
            claim, "", {1: "Содержание а) Введение ... 3", 17: "и) Качество воды"}
        )
        is None
    )
    assert (
        structural_counterevidence("Отсутствует пункт «к»", "", {1: "и) Перечень"})
        is None
    )


def test_seismic_overlapping_range_and_unspecified_specification_groups_are_not_proven_conflicts():
    """Восемь входит в 8–9; совпадение марки не доказывает равенство количеств разных групп."""
    a = fact("8", prop="Сейсмичность", unit="")
    b = fact("8–9", 2, prop="Сейсмичность", unit="")
    assert (
        evidence_audit(audit(candidate(a, b), a, b))["reason_code"]
        == "compatible_seismic_range"
    )
    a = fact("2", prop="Количество", unit="шт", identifier="МС-4")
    b = fact("4", 2, prop="Количество", unit="шт", identifier="МС-4")
    assert (
        evidence_audit(audit(candidate(a, b), a, b))["reason_code"]
        == "specification_group_not_established"
    )
