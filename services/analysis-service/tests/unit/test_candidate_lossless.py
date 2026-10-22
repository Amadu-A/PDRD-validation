# services/analysis-service/tests/unit/test_candidate_lossless.py

"""Регрессии потерь независимых претензий и межстраничных кандидатов."""

from types import SimpleNamespace

import pytest
from pdrd_analysis_service.application.use_cases.common import (
    select_violation_candidates,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentContextOptions,
    DocumentPage,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.application.use_cases.normative import CheckPageAgainstNorms
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
)
from pdrd_analysis_service.domain.engineering_claims import false_numeric_claim

from .test_analysis_use_cases import FakeVisionModel, page_facts
from .test_engineering_review import _fact


@pytest.mark.parametrize(
    "property_claim",
    [
        "обозначение Н1 не совпадает с Н2 в спецификации",
        "модель насоса Н1 не совпадает с моделью в спецификации",
        "материал корпуса насоса Н1 не соответствует спецификации",
        "в спецификации обозначение Н1 не совпадает с Н2",
        "давление корпуса насоса Н1 не соответствует паспорту",
    ],
)
def test_correct_conversion_does_not_discard_independent_claim(property_claim):
    """Правильный пересчёт рядом с иной претензией не является её опровержением."""
    comment = f"Насос: расход 1 л/с (3,6 м3/ч), но {property_claim}."
    assert false_numeric_claim(comment) is None
    selected = select_violation_candidates([{"comment": comment, "evidence": "Н1; Н2"}])
    assert len(selected.candidates) == 1


def test_false_arithmetic_is_removed_but_independent_claim_and_origin_survive():
    """Ложное вычисление исключается из вывода, исходная запись доступна для аудита."""
    comment = "Расход 1 л/с соответствует 36 м3/ч, но обозначение Н1 не совпадает с Н2 в спецификации."
    selected = select_violation_candidates([{"comment": comment, "evidence": "Н1; Н2"}])
    assert len(selected.candidates) == 1
    assert "36 м3/ч" not in selected.candidates[0]["comment"]
    assert "Н1 не совпадает с Н2" in selected.candidates[0]["comment"]
    assert selected.origin_assertions_by_candidate[0][0]["comment"] == comment


async def test_independent_claim_reaches_norms_output_and_finalization_with_diagnostics():
    """Независимая претензия доходит до финализации, ложный пересчёт остаётся только в аудите."""
    comment = "Расход 1 л/с соответствует 36 м3/ч, но обозначение Н1 не совпадает с Н2 в спецификации."
    model = FakeVisionModel(
        [
            {
                "summary": "Проверено",
                "violations": [
                    {
                        "comment": comment,
                        "evidence": "Н1; Н2",
                        "category": "marking",
                        "object_ref": "насос",
                        "status": "needs_review",
                        "confidence": 0.8,
                        "normative_source_ids": [],
                    }
                ],
            }
        ]
    )
    _, findings, metrics = await CheckPageAgainstNorms(model, 2600, 10, 700).execute(
        page_number=1,
        extracted_text="Н1; Н2",
        page_facts=page_facts(),
        normative_sources=(),
        image_bytes=b"test",
    )
    assert len(findings) == 1
    record = metrics.as_dict()["candidate_outcomes"][0]
    assert (
        record["disposition"] == "saved"
        and record["finding_id"] == findings[0].finding_id
    )
    assert record["payload"]["comment"] == comment
    assert record["refuted_claims"][0]["reason"] == "false_unit_conversion"
    assert "normalized_quantities" in record["refuted_claims"][0]
    final_model = FakeVisionModel(
        [
            {
                "summary": "Проверено",
                "findings": [
                    {
                        "finding_id": findings[0].finding_id,
                        "decision": "keep",
                        "comment": findings[0].comment,
                        "status": "needs_review",
                        "recommendation": "Сверить обозначения насоса со спецификацией.",
                        "confidence": 0.8,
                    }
                ],
            }
        ]
    )
    _, final, _ = await FinalizeFindings(final_model, 1000, 10, 600, 0.55).execute(
        findings=findings,
        experience_by_finding={},
    )
    assert len(final) == 1 and "36 м3/ч" not in final[0].comment
    assert "Н1 не совпадает с Н2" in final[0].comment


def test_merge_targets_original_candidate_after_false_arithmetic_was_removed():
    """Цель объединения указывает на существующий исходный ID, а не на хеш переписанного текста."""
    original = "Расход 1 л/с соответствует 36 м3/ч, но обозначение Н1 не совпадает с Н2 в спецификации."
    base = {"object_ref": "насос Н1", "evidence": "Н1; Н2", "category": "marking"}
    selected = select_violation_candidates(
        [
            {**base, "comment": original},
            {**base, "comment": "обозначение Н1 не совпадает с Н2 в спецификации."},
        ]
    )
    assert len(selected.candidates) == 1
    first, second = selected.candidate_outcomes
    assert (
        second["disposition"] == "merged"
        and second["target_id"] == first["candidate_id"]
    )


@pytest.mark.parametrize(
    "comment",
    [
        "Расход 1 л/с соответствует 36 м3/ч; исправить пересчёт.",
        "Расход 1 л/с (3,6 м3/ч).",
        "Расход 1 л/с соответствует 3,6 м3/ч.",
    ],
)
def test_arithmetic_without_independent_claim_never_becomes_finding(comment):
    """Неверное вычисление и верное равенство сами по себе не образуют нарушение."""
    selected = select_violation_candidates([{"comment": comment}])
    assert selected.candidates == ()
    assert selected.candidate_outcomes[0]["disposition"] == "refuted"


def conflict_pages(count=1):
    """Два исходных представления независимых насосов, без готовых замечаний."""
    pages = []
    for number, value in [(1, "10"), (2, "12")]:
        facts = tuple(
            _fact(
                value,
                identifier=f"P{index:02d}",
                subject_name=f"Насос P{index:02d}",
                property_name="Мощность",
                unit_raw="кВт",
                extraction_method="pdf_text",
                evidence=f"Насос P{index:02d}: мощность {value} кВт.",
            )
            for index in range(count)
        )
        pages.append(
            DocumentPage(
                number, "\n".join(fact.evidence_text for fact in facts), "", facts
            )
        )
    return tuple(pages)


class DecisionVision:
    """Возвращает только решения; исходные кандидаты строит реальная программа."""

    def __init__(self, decisions):
        """Запоминает ответы и фактическое количество предъявленных кандидатов."""
        self.decisions, self.checked = decisions, []

    async def generate_json(self, **request):
        """Не делает сетевых вызовов и не возвращает готовых замечаний."""
        ids = request["schema"]["properties"]["decisions"]["items"]["properties"][
            "candidate_id"
        ]["enum"]
        self.checked.extend(ids)
        return SimpleNamespace(payload={"decisions": self.decisions})


@pytest.mark.parametrize(
    "decisions",
    [
        [],
        None,
        {},
        [None, 4],
        [
            {"candidate_id": "C1", "status": "rejected", "reason": ""},
        ],
    ],
)
async def test_missing_or_malformed_decision_keeps_full_unassessed_candidate(decisions):
    """Молчание и повреждённое решение не опровергают факты и не подтверждают нарушение."""
    diagnostics = {}
    findings = await CheckCrossPageConsistency(
        DecisionVision(decisions),
        DocumentContextOptions(3, 2, 0.5, 24, 5, 24, 1000),
    ).execute(pages=conflict_pages(), diagnostics=diagnostics)
    assert not [f for f in findings if f.finding_id.startswith("cross-page-")]
    (record,) = diagnostics["candidates"]
    assert record["disposition"] == "unassessed"
    assert record["reason"] == "missing_decision"
    assert {item["page"] for item in record["facts"]} == {1, 2}
    assert {item["fact"]["value_raw"] for item in record["facts"]} == {"10", "12"}
    assert all(item["fact"]["identifier"] == "P00" for item in record["facts"])


async def test_partial_and_reasoned_rejected_decisions_are_distinct():
    """Одна аргументированная отмена не распространяется на неоценённый соседний объект."""
    diagnostics = {}
    decision = {
        "candidate_id": "C1",
        "status": "rejected",
        "reason": "В паспорте указана иная комплектация.",
        "reason_code": "different_condition",
    }
    await CheckCrossPageConsistency(
        DecisionVision([decision]), DocumentContextOptions(3, 2, 0.5, 24, 5, 24, 1000)
    ).execute(
        pages=conflict_pages(2),
        diagnostics=diagnostics,
    )
    assert [row["disposition"] for row in diagnostics["candidates"]] == [
        "refuted",
        "unassessed",
    ]
    assert diagnostics["candidates"][0]["decision"] == decision


async def test_unexpected_program_error_is_not_masked_as_empty_decision():
    """Программная ошибка поднимается вызывающей стороне, а не выдаётся за молчание модели."""

    class BrokenVision:
        """Имитирует дефект адаптера, отличный от неполного модельного ответа."""

        async def generate_json(self, **request):
            """Исключение намеренно не переводится в пустой результат."""
            raise RuntimeError("adapter bug")

    with pytest.raises(RuntimeError, match="adapter bug"):
        await CheckCrossPageConsistency(
            BrokenVision(), DocumentContextOptions(3, 2, 0.5, 24, 5, 24, 1000)
        ).execute(
            pages=conflict_pages(),
            diagnostics={},
        )


def test_discovery_registers_overflow_and_is_order_independent():
    """Бюджет проверки 24 не стирает двадцать пятый обнаруженный конфликт."""
    pages = conflict_pages(25)
    facts = tuple(
        LocatedDocumentFact(p.page, i, f)
        for p in pages
        for i, f in enumerate(p.facts, 1)
    )
    before = discover_conflicts(facts, max_candidates=24)
    after = discover_conflicts(tuple(reversed(facts)), max_candidates=24)
    assert len(before) == len(after) == 25
    assert before == after


async def test_budget_defers_full_payload_without_more_model_work():
    """Судьбы всех 25 групп воспроизводимы, модель получает только бюджет 24."""
    outcomes = []
    for pages in [
        conflict_pages(25),
        tuple(reversed(conflict_pages(25))),
        conflict_pages(25),
    ]:
        diagnostics = {}
        vision = DecisionVision([])
        await CheckCrossPageConsistency(
            vision, DocumentContextOptions(3, 2, 0.5, 24, 5, 24, 1000)
        ).execute(
            pages=pages,
            diagnostics=diagnostics,
        )
        assert len(vision.checked) == 24
        assert diagnostics["counts"] == {
            "discovered": 25,
            "checked": 24,
            "deferred": 1,
            "refuted": 0,
            "saved": 0,
            "unassessed": 24,
        }
        deferred = [
            r for r in diagnostics["candidates"] if r["disposition"] == "deferred"
        ]
        assert len(deferred[0]["facts"]) == 2
        assert deferred[0]["reason"] == "validation_budget_exhausted"
        outcomes.append(diagnostics)
    assert outcomes[0] == outcomes[1] == outcomes[2]
