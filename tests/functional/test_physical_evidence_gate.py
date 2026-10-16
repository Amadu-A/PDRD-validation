# tests/functional/test_physical_evidence_gate.py

"""Сквозная проверка геометрических сравнений, повторов и выхода Finalization."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

from pdrd_analysis_service.application.evidence_audit import (
    audit_findings_evidence,
    evidence_audit,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    audit_document_findings,
    build_page_document_context,
    document_engineering_report,
    merge_cross_page_findings,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import GenerationMetrics
from pdrd_analysis_service.domain.engineering_assertion import read_assertion

from tests.functional.test_engineering_evidence_gate import (
    UnavailableVision,
    audit,
    candidate,
    fact,
    finalize,
)


def test_rephrased_purpose_conflicts_merge_without_losing_observations_or_independent_question():
    """Полный конфликт поглощает неполное сравнение; вопрос расчёта той же сети остаётся."""
    a = fact(
        "водоотведение",
        prop="Назначение системы",
        unit="",
        identifier="S1",
        evidence_text="Система S1 водоотведение",
    )
    b = fact(
        "водоснабжение",
        2,
        prop="Назначение системы",
        unit="",
        identifier="S1",
        evidence_text="Система S1 водоснабжение",
    )
    c = replace(b, page=3)
    complete = audit(candidate(a, b, finding_id="complete"), a, b)
    repeated = audit(candidate(a, c, finding_id="repeated"), a, c)
    partial = audit(
        replace(
            candidate(a, claim_type="missing_justification", finding_id="partial"),
            comment="Уточнить назначение системы S1.",
        ),
        a,
    )
    flow = fact("10", prop="Расход воды", unit="л/с")
    independent = audit(
        candidate(flow, claim_type="missing_justification", finding_id="why-flow"), flow
    )
    result = merge_cross_page_findings(
        page_findings={1: (complete, partial, independent), 3: (repeated,)},
        cross_findings=(),
    )
    findings = [item for items in result.values() for item in items]
    assert len(findings) == 2
    conflict = next(item for item in findings if item.finding_id != "why-flow")
    assert {
        item.page for item in read_assertion(conflict.origin_assertions).observations
    } == {1, 2, 3}
    assert {
        origin["finding_id"]
        for origin in conflict.origin_assertions
        if origin.get("origin") == "merged_finding"
    } >= {"partial", "complete"}


def test_same_object_independent_justification_is_never_subsumed_by_conflict():
    """Общее число не поглощает самостоятельную претензию об исходных условиях расчёта."""
    a = fact("10", prop="Расход воды", unit="л/с")
    b = fact("12", 2, prop="Расход воды", unit="л/с")
    complete = audit(candidate(a, b), a, b)
    why = audit(candidate(a, claim_type="missing_justification", finding_id="why"), a)
    result = merge_cross_page_findings(
        page_findings={1: (complete, why)}, cross_findings=()
    )
    assert len(result[1]) == 2


async def test_existing_fire_flow_and_hydraulic_questions_survive_geometry_dedup_and_finalization():
    """Сохраняются поступившие предметные вопросы о принятом расходе и гидравлических предпосылках."""
    flow = fact("10", prop="Расход наружного пожаротушения", unit="л/с")
    diameter = fact("Ø110x10", prop="Диаметр", unit="")
    questions = []
    for item, identifier, text in (
        (
            flow,
            "fire",
            "Обосновать расчётный расход наружного пожаротушения S1 10 л/с по исходным условиям объекта.",
        ),
        (
            diameter,
            "hydraulic",
            "Уточнить расчёт скоростей и потерь напора S1 для принятого диаметра Ø110x10.",
        ),
    ):
        original = replace(
            candidate(item, claim_type="missing_justification", finding_id=identifier),
            comment=text,
        )
        questions.append(audit(original, item))
    merged = merge_cross_page_findings(
        page_findings={1: tuple(questions)}, cross_findings=()
    )
    _, final, metrics = await finalize(*merged[1])
    assert [item.comment for item in final] == [item.comment for item in questions]
    assert all(item.status == "needs_review" for item in final)
    assert metrics["vlm_call_count"] == 0


async def test_generated_equivalent_size_conflict_is_checked_after_actual_finalization_output():
    """Даже неструктурированный вход не позволяет модели обойти повторную проверку."""
    a, b = fact("Ø110x10", unit=""), fact("Ø110*10", unit="")
    original = replace(
        candidate(a, b),
        origin_assertions=(
            {
                "origin": "engineering_context",
                "context": {
                    "page": 1,
                    "facts": [
                        {
                            **asdict(item.fact),
                            "subject_type": "",
                            "property_name": "pipe_diameter",
                        }
                        for item in (a, b)
                    ],
                },
            },
        ),
        comment="Проверить обозначения S1",
    )
    original = audit_findings_evidence(
        (original,),
        page_texts={1: a.fact.evidence_text + "\n" + b.fact.evidence_text},
        stage="document",
    )[0]
    assert read_assertion(original.origin_assertions) is None

    class EditingModel:
        """Меняет именно текст выхода модели; готовый итог проверки не подставляется."""

        async def generate_json(self, **request):
            """Возвращает ложное сравнение для программной проверки после генерации."""
            return SimpleNamespace(
                payload={
                    "findings": [
                        {
                            "finding_id": original.finding_id,
                            "decision": "keep",
                            "comment": "Для S1 выявлено расхождение диаметров Ø110x10 и Ø110*10.",
                            "recommendation": "Сверить диаметры S1",
                        }
                    ]
                },
                metrics=GenerationMetrics(1, "stop", 0, 0, 0, 0, 0, 0, 0),
            )

    _, final, metrics = await FinalizeFindings(
        EditingModel(), 1500, 8, 1000, 0.5
    ).execute(findings=(original,), experience_by_finding={})
    assert final == ()
    assert metrics["vlm_call_count"] == 1
    assert (
        metrics["rejected_findings"][0]["stage"] == "post_finalization_evidence_audit"
    )
    assert (
        metrics["rejected_findings"][0]["reason_code"]
        == "equivalent_values_or_compatible_descriptions"
    )


def test_original_reference_is_joined_in_context_registry_and_audited_without_norm_search():
    """СП 31.13330 передаётся с двумя физическими источниками, не исправлением строки в отчёте."""
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures/engineering/stage3-reference-continuation.json"
    )
    texts = {p["page"]: p["text"] for p in json.loads(path.read_text("utf-8"))["pages"]}
    pages = tuple(DocumentPage(number, text, "", ()) for number, text in texts.items())
    report = document_engineering_report(pages, "reference-fixture")
    assert any(
        item.observation.fact.value_raw == "СП 31.13330"
        and item.grounding == "literal_text"
        for item in report.registry.evidence
    )
    local_report = document_engineering_report(pages, "reference-local", enabled=False)
    assert not any(
        item.observation.fact.source_fragments
        for item in local_report.registry.evidence
    )
    options = SimpleNamespace(
        max_related_facts_per_page=12,
        max_text_sources_per_page=3,
        semantic_threshold=0.5,
    )
    contexts = build_page_document_context(
        pages=pages, semantic_by_page={}, options=options
    )
    for number in (16, 17):
        sources = [
            source
            for source in contexts[number]
            if source.match_type == "reference_continuation"
        ]
        assert {source.page for source in sources} == {16, 17}
        assert all("31.13330" in source.text for source in sources)
    item = fact("СП", 16, prop="Нормативная ссылка", unit="")
    original = replace(
        candidate(item),
        origin_assertions=(),
        comment="Неполная нормативная ссылка СП без номера",
        evidence="согласно СП",
    )
    checked = audit_document_findings({16: (original,)}, report, page_texts=texts)[16][
        0
    ]
    assert (
        evidence_audit(checked)["reason_code"]
        == "normative_reference_continues_on_next_page"
    )
    assert checked.status == "hypothesis"


async def test_real_total_length_conflict_survives_final_check_and_model_failure():
    """Истинное межстраничное расхождение не теряется при проверке геометрии после модели."""
    a = fact("147", prop="Длина", unit="м", evidence_text="S1 протяженность сети 147 м")
    b = fact(
        "150",
        2,
        prop="Длина",
        unit="м",
        evidence_text="S1 общая протяженность сети 150 м",
    )
    checked = audit(candidate(a, b), a, b)
    _, final, metrics = await finalize(checked, model=UnavailableVision())
    assert len(final) == 1 and "147" in final[0].comment and "150" in final[0].comment
    assert metrics["fallback_count"] == 1
