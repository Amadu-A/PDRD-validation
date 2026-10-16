# tests/functional/test_engineering_evidence_gate.py

"""Кандидат → исходные доказательства → объединение → финализация без новых вызовов."""

from dataclasses import asdict, replace

import fitz
import pytest
from pdrd_analysis_service.application.evidence_audit import (
    audit_findings_evidence,
    evidence_audit,
)
from pdrd_analysis_service.application.ports.vision_model import VisionModelError
from pdrd_analysis_service.application.use_cases.document_context import (
    merge_cross_page_findings,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import (
    DocumentContextSource,
    ExperienceSource,
    FindingDraft,
    FindingVisualRegion,
    NormativeSource,
)
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_assertion import assertion_from_facts

from tests.functional.test_review_api import client, command
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow


def fact(value, page=1, *, prop="Диаметр", unit="мм", identifier="S1", **extra):
    """Задаёт нейтральное исходное утверждение с известной физической страницей."""
    raw = document_fact_from_mapping(
        {
            "kind": "parameter",
            "subject_type": "system",
            "identifier": identifier,
            "property_type": "parameter",
            "property_name": prop,
            "value_raw": value,
            "unit_raw": unit,
            "evidence_text": f"{identifier} {prop}: {value} {unit}",
            **extra,
        }
    )
    return LocatedDocumentFact(page, 1, raw)


def candidate(*items, claim_type="contradiction", finding_id="candidate-1", **extra):
    """Имитирует ответ предыдущего этапа; проверяющий механизм не получает эталонный вывод."""
    assertion = assertion_from_facts(items, claim_type=claim_type, include_related=True)
    return FindingDraft(
        finding_id,
        items[0].page,
        "document",
        "document_consistency",
        "warning",
        "confirmed",
        "Выявлено расхождение параметра",
        "; ".join(item.fact.evidence_text for item in items),
        "Проверить проектное решение",
        0.9,
        (),
        "",
        (),
        "",
        origin_assertions=(
            assertion.as_origin(),
            {"origin": "vlm", "candidate_id": "raw-1"},
        ),
        object_ref=items[0].fact.identifier,
        **extra,
    )


def audit(original, *items):
    """Передаёт действительные страницы, не используя текст самого кандидата как источник."""
    return audit_findings_evidence(
        (original,),
        page_texts={item.page: item.fact.evidence_text for item in items},
        stage="test_source_audit",
    )[0]


class NoVision:
    """Контроль доказательств и защищённых вопросов выполняется без модели."""

    async def generate_json(self, **request):
        """Любая генерация означает нарушение бюджета теста."""
        raise AssertionError("Дополнительный VLM-вызов")


class UnavailableVision:
    """Существующая финализация может быть недоступна; проверенные данные сохраняются."""

    async def generate_json(self, **request):
        """Воспроизводит резервную финализацию вместо нового анализатора."""
        raise VisionModelError("Временно недоступна модель")


async def finalize(*findings, model=None):
    """Вызывает действующий сценарий, включая обычный и резервный пути."""
    return await FinalizeFindings(model or NoVision(), 1500, 8, 1000, 0.5).execute(
        findings=findings, experience_by_finding={}
    )


@pytest.mark.parametrize(
    "values,reason",
    [
        (("DN100", "Ø110"), "different_parameter_definitions"),
        (("Ø110x10", "Ø110*10"), "equivalent_values_or_compatible_descriptions"),
    ],
)
async def test_false_comparison_kept_in_diagnostics_with_original_ids_text_sources_and_geometry(
    values, reason
):
    """Диагностика не попадает в пользовательские findings и не теряет исходный кандидат."""
    a, b = fact(values[0]), fact(values[1], 2)
    region = FindingVisualRegion(10, 20, 100, 60, 0.9)
    original = candidate(
        a,
        b,
        visual_regions=(region,),
        evidence_locations=({"page": 2, "source_id": "D2", "text": "Вторая область"},),
    )
    guarded = audit(original, a, b)
    _, final, metrics = await finalize(guarded)
    assert final == () and metrics["vlm_call_count"] == 0
    diagnostic = metrics["rejected_findings"][0]
    assert diagnostic["reason_code"] == reason
    saved = diagnostic["candidate"]
    assert saved["finding_id"] == original.finding_id
    assert saved["evidence_locations"] == original.evidence_locations
    assert saved["visual_regions"] == (asdict(region),)
    assert any(
        origin.get("candidate_id") == "raw-1" for origin in saved["origin_assertions"]
    )
    assert evidence_audit(guarded)["original"]["comment"] == original.comment


async def test_missing_second_wall_value_yields_question_using_only_supported_side():
    """Неподтверждённые 6,6 мм не становятся прочитанным значением или нарушением."""
    a, invented = fact("Ø110x10"), fact("Ø110x6,6", 2)
    actual = replace(
        invented, fact=replace(invented.fact, evidence_text="S1 Диаметр: Ø110x10 мм")
    )
    guarded = audit(candidate(a, invented), a, actual)
    assert guarded.status == "needs_review"
    assert "Ø110x10" in guarded.comment and "6,6" not in guarded.comment
    _, final, metrics = await finalize(guarded)
    assert final[0].status == "needs_review" and metrics["vlm_call_count"] == 0
    assert "6,6" not in final[0].evidence
    assert "6,6" in evidence_audit(guarded)["original"]["evidence"]


async def test_unestablished_segment_question_keeps_both_original_locations():
    """Две длины одного ввода сохраняются вопросом, а не доказанным противоречием."""
    a, b = fact("15", prop="Длина", unit="м"), fact("24", 2, prop="Длина", unit="м")
    original = candidate(
        a,
        b,
        evidence_locations=(
            {"page": 1, "text": a.fact.evidence_text},
            {"page": 2, "text": b.fact.evidence_text},
        ),
    )
    guarded = audit(original, a, b)
    _, final, metrics = await finalize(guarded)
    assert (
        final[0].status == "needs_review"
        and final[0].evidence_locations == original.evidence_locations
    )
    assert "15" in final[0].comment and "24" in final[0].comment
    assert metrics["vlm_call_count"] == 0


async def test_internal_diagnostic_without_engineering_subject_is_not_in_user_report():
    """Нехватка engineering_context не заменяется выдуманным проектным замечанием."""
    item = fact("Ø110x10")
    original = replace(
        candidate(item),
        origin_assertions=(),
        comment="Недостаточно evidence в engineering_context",
    )
    _, final, metrics = await finalize(original)
    assert (
        final == ()
        and metrics["rejected_findings"][0]["candidate"]["comment"] == original.comment
    )
    assert metrics["vlm_call_count"] == 0


async def test_legacy_context_is_audited_with_original_source_not_model_quote():
    """Старый DTO без engineering_assertion проходит ту же проверку типоразмеров."""
    a, b = fact("Ø110x10"), fact("Ø110*10", 2)
    original = replace(
        candidate(a, b),
        comment="Различается диаметр S1",
        origin_assertions=tuple(
            {
                "origin": "engineering_context",
                "context": {"page": item.page, "facts": [asdict(item.fact)]},
            }
            for item in (a, b)
        ),
    )
    _, final, metrics = await finalize(audit(original, a, b))
    assert (
        final == ()
        and metrics["rejected_findings"][0]["reason_code"]
        == "equivalent_values_or_compatible_descriptions"
    )


async def test_honest_justification_questions_preserve_fire_flow_and_pipe_choice_without_claiming_absence():
    """Предметные вопросы о принятом расходе и диаметре защищены при отсутствии расчётных оснований."""
    for item in (fact("10", prop="Расчётный расход воды", unit="л/с"), fact("Ø110x10")):
        original = candidate(item, claim_type="missing_justification")
        guarded = audit(original, item)
        _, final, metrics = await finalize(guarded)
        assert (
            final[0].status == "needs_review"
            and item.fact.value_raw in final[0].comment
        )
        assert "отсутств" not in final[0].comment and metrics["vlm_call_count"] == 0


async def test_real_discrepancy_survives_fallback_and_dedup_with_sources_and_decision_history():
    """Одно реальное расхождение объединяется по всему предмету, сохраняя все доказательства."""
    a, b = (
        fact("147", prop="Общая протяженность сети", unit="м"),
        fact("150", 2, prop="Общая протяженность сети", unit="м"),
    )
    original = audit(candidate(a, b), a, b)
    other = replace(
        original,
        finding_id="second-stage",
        page=2,
        confidence=0.65,
        document_context_source_ids=("D-p0002-f0001",),
        document_context_basis_sources=(
            DocumentContextSource(
                "D-p0002-f0001", 2, evidence_text=b.fact.evidence_text
            ),
        ),
        evidence_locations=({"page": 2, "text": b.fact.evidence_text},),
    )
    merged = merge_cross_page_findings(
        page_findings={1: (original,)}, cross_findings=(other,)
    )
    drafts = tuple(item for items in merged.values() for item in items)
    assert len(drafts) == 1 and drafts[0].document_context_source_ids == (
        "D-p0002-f0001",
    )
    history = next(
        origin
        for origin in drafts[0].origin_assertions
        if origin.get("origin") == "merged_finding"
    )
    assert (
        history["finding_id"] == original.finding_id
        and history["confidence"] == original.confidence
    )
    _, final, metrics = await finalize(*drafts, model=UnavailableVision())
    assert len(final) == 1 and "147" in final[0].comment and "150" in final[0].comment
    assert final[0].evidence_locations == drafts[0].evidence_locations
    assert metrics["vlm_call_count"] == 1 and metrics["fallback_count"] == 1


def test_guaranteed_and_required_head_questions_remain_independent():
    """Один объект не объединяет независимые расчётные роли напора."""
    a, b = (
        fact("32", prop="Гарантированный напор", unit="м"),
        fact("45", prop="Требуемый напор", unit="м"),
    )
    left = audit(candidate(a, claim_type="missing_justification"), a)
    right = audit(
        candidate(b, claim_type="missing_justification", finding_id="required"), b
    )
    result = merge_cross_page_findings(
        page_findings={1: (left, right)}, cross_findings=()
    )
    assert len(result[1]) == 2 and all(
        item.status == "needs_review" for item in result[1]
    )


async def test_audited_question_keeps_accepted_review_geometry_and_exported_pdf(
    pdf_flow,
):
    """Новый программный вопрос проходит прежний Review, сохраняет принятую область и настоящий PDF."""
    item = fact("10", prop="Расчётный расход воды", unit="л/с")
    guarded = audit(
        candidate(item, claim_type="missing_justification", finding_id="vlm:1"), item
    )
    _, final, metrics = await finalize(guarded)
    assert metrics["vlm_call_count"] == 0
    f = pdf_flow

    async def result(*, document_id):
        """Подставляет только результат анализа, сохраняя все настоящие сервисы Review и экспорта."""
        return {"findings": [asdict(final[0])]}

    f.artifacts.load_result = result
    async with client(f) as browser:
        opened = await browser.post(f.endpoint + "/open")
        assert opened.status_code == 200, opened.text
        accepted = await command(
            browser,
            f,
            opened.json()["revision"],
            action="decide",
            finding_id="vlm:1",
            decision="accepted",
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["area_confirmations"][0]["valid"]
        approved = await command(
            browser, f, accepted.json()["revision"], action="approve"
        )
        assert approved.status_code == 200, approved.text
        restored = await browser.get(f.endpoint)
        assert restored.json()["findings"] == approved.json()["findings"]
        assert (
            restored.json()["area_confirmations"]
            == approved.json()["area_confirmations"]
        )
        exported = await browser.post(
            f.pdf_endpoint, json={"expected_revision": approved.json()["revision"]}
        )
        assert exported.status_code == 200, (
            exported.text if exported.status_code != 200 else ""
        )
        with fitz.open(stream=exported.content, filetype="pdf") as pdf:
            text = "\n".join(page.get_text() for page in pdf)
            assert "10" in text and "S1" in text
            assert "engineering_context" not in text
            assert any(annotation.type[1] == "Text" for annotation in pdf[0].annots())


async def test_specific_temperature_question_is_not_replaced_by_flow_question():
    """Наличие ГВС с принятым расходом позволяет сохранить предметный вопрос о температуре."""
    item = fact("5,086", prop="Расход воды на приготовление ГВС", unit="м³/ч")
    original = replace(
        candidate(item, claim_type="missing_justification"),
        comment="Указать принятую расчётную температуру ГВС для S1 при расходе 5,086 м³/ч.",
        recommendation_draft="Уточнить исходную температуру, используемую в расчёте приготовления ГВС.",
    )
    guarded = audit(original, item)
    _, final, metrics = await finalize(guarded)
    assert final[0].comment == original.comment and final[0].status == "needs_review"
    assert metrics["vlm_call_count"] == 0


async def test_unavailable_requested_norm_keeps_source_id_and_specific_question_without_enrichment_call():
    """Недоступный N отличается от доказанного нарушения и не вызывает модель ради восстановления основания."""
    a, b = (
        fact("10", prop="Расход воды", unit="л/с"),
        fact("12", 2, prop="Расход воды", unit="л/с"),
    )
    original = replace(candidate(a, b), normative_source_ids=("N-missing",))
    guarded = audit(original, a, b)
    _, final, metrics = await finalize(guarded)
    assert final[0].status == "needs_review"
    assert evidence_audit(guarded)["status"] == "source_unavailable"
    assert evidence_audit(guarded)["missing_source_ids"] == ("N-missing",)
    assert metrics["vlm_call_count"] == 0


async def test_related_modes_in_justification_context_do_not_turn_questions_into_diagnostics():
    """Контекст двух режимов не является сравнением; исходные вопросы не сливаются и не пропадают."""
    a = fact(
        "10",
        prop="Расход воды",
        unit="л/с",
        scope_operating_mode="рабочий",
        evidence_text="S1 Расход воды 10 л/с рабочий",
    )
    b = fact(
        "12",
        2,
        prop="Расход воды",
        unit="л/с",
        scope_operating_mode="аварийный",
        evidence_text="S1 Расход воды 12 л/с аварийный",
    )
    first = replace(
        candidate(a, b, claim_type="missing_justification"),
        comment="Уточнить обоснование рабочего расхода S1 10 л/с.",
    )
    second = replace(
        first,
        finding_id="emergency",
        comment="Уточнить обоснование аварийного расхода S1 12 л/с.",
    )
    guarded = tuple(audit(item, a, b) for item in (first, second))
    merged = merge_cross_page_findings(page_findings={1: guarded}, cross_findings=())
    _, final, metrics = await finalize(*merged[1])
    assert len(final) == 2 and all(item.status == "needs_review" for item in final)
    assert {item.comment for item in final} == {first.comment, second.comment}
    assert metrics["vlm_call_count"] == 0


async def test_skipping_editor_keeps_retrieved_norm_candidate_without_faking_applicability():
    """Нулевой бюджет вопросов не удаляет найденные N и не выдаёт их за проверенную норму."""
    item = fact("10", prop="Расход воды", unit="л/с")
    guarded = audit(candidate(item, claim_type="missing_justification"), item)
    source = NormativeSource(
        "N-extra", "point", 0.8, "СП.pdf", None, 7, 0, "Найденный нормативный фрагмент"
    )
    alias = replace(source, source_id="N-alias")
    experience = ExperienceSource(
        "E-extra",
        "e-point",
        0.8,
        None,
        None,
        "Пример вопроса",
        None,
        False,
        1,
        None,
        "Исходный контекст",
        "",
    )
    _, final, metrics = await FinalizeFindings(NoVision(), 1500, 8, 1000, 0.5).execute(
        findings=(guarded,),
        experience_by_finding={guarded.finding_id: (experience,)},
        normative_candidates_by_finding={guarded.finding_id: (source, alias)},
    )
    context = next(
        origin
        for origin in final[0].origin_assertions
        if origin.get("origin") == "unverified_enrichment_context"
    )
    assert context["normative_candidates"] == [asdict(source), asdict(alias)]
    assert context["experience_candidates"] == [asdict(experience)]
    assert final[0].basis_sources == () and final[0].status == "needs_review"
    assert metrics["vlm_call_count"] == 0


async def test_legacy_hypothesis_does_not_leak_pipeline_diagnostics_or_get_promoted():
    """Даже отложенная гипотеза не показывает внутренние поля и не повышается без доказательства."""
    item = fact("Ø110x10")
    original = replace(
        candidate(item),
        status="hypothesis",
        comment="Недостаточно evidence в engineering_context",
    )
    guarded = audit(original, item)
    _, final, metrics = await finalize(guarded)
    assert final[0].status == "hypothesis" and metrics["vlm_call_count"] == 0
    assert (
        "engineering_context" not in final[0].comment
        and "evidence" not in final[0].comment
    )
    assert evidence_audit(guarded)["original"]["comment"] == original.comment
