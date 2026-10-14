# services/analysis-service/tests/unit/test_engineering_review.py

"""Регрессии инженерных вопросов, доказательств и известных классов ложных претензий."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pdrd_analysis_service.application.engineering_review import engineering_questions
from pdrd_analysis_service.application.use_cases.common import (
    select_violation_candidates,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentContextOptions,
    DocumentPage,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import PageFacts
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_claims import false_numeric_claim


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("75,45 м меньше 60 м", "false_numeric_comparison"),
        ("2 м больше 2500 мм", "false_numeric_comparison"),
        ("600 кПа ниже 0,5 МПа", "false_numeric_comparison"),
        ("10 октября — 10 мая составляет 7,5 месяца", "false_calendar_duration"),
        ("10 октября — 10 мая составляет 7 месяцев", None),
        ("75,45 м больше 60 м", None),
        ("0,205 л/с меньше 1 м³/ч", None),
        ("Запись «75,45 м меньше 60 м» содержит ошибочное сравнение.", None),
        ("В1 требует 60 м, В2 требует 75,45 м.", None),
    ],
)
def test_numeric_claims_use_units_and_calendar(text, reason) -> None:
    """Подтверждённая ошибка отсекается; разные параметры и цитаты не подменяются."""
    assert false_numeric_claim(text) == reason


def _fact(value, evidence="Система S1, расчётное значение", **extra):
    """Создаёт нейтральный атомарный факт, не связанный с контрольным PDF."""
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "identifier": "S1",
            "subject_type": "system",
            "subject_name": "Система S1",
            "property_type": "parameter",
            "property_name": "Параметр",
            "value_raw": value,
            "evidence_text": evidence,
            **extra,
        }
    )
    assert fact is not None
    return fact


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_fact("Ду100"), _fact("Ø110")),
        (_fact("7", "ОСР-2015-А: 7 баллов"), _fact("8", "ОСР-2015-В: 8 баллов")),
        (
            _fact("0,5", scope_operating_mode="рабочий"),
            _fact("1,0", scope_operating_mode="аварийный"),
        ),
        (
            _fact("20", property_name="Сумма части строк"),
            _fact("40", property_name="Итого всех строк"),
        ),
        (
            _fact("сталь", property_name="Материал корпуса"),
            _fact("обрезиненный клин", property_name="Уплотнение"),
        ),
    ],
)
def test_incomparable_parameters_do_not_create_conflicts(first, second) -> None:
    """Различия типа диаметра, карты, режима, полноты таблицы и назначения не являются дефектом."""
    located = (LocatedDocumentFact(1, 1, first), LocatedDocumentFact(2, 1, second))
    assert discover_conflicts(located, max_candidates=10) == ()


def test_false_claim_diagnostics_and_exact_object_duplicates() -> None:
    """Проверка вычислений сохраняет все корректные кандидаты и происхождение повторов."""
    valid = {
        "comment": "Неверная ссылка на таблицу",
        "evidence": "Ссылка 2, заголовок 3",
        "object_ref": "table-3",
        "normative_source_ids": ["N1"],
    }
    other = {**valid, "object_ref": "table-7"}
    selected = select_violation_candidates(
        [
            valid,
            {**valid, "normative_source_ids": ["N2"]},
            other,
            {"comment": "75,45 м меньше 60 м", "evidence": "75,45 м; 60 м"},
        ]
    )
    assert selected.generated_count == 4
    assert selected.consolidated_count == 2
    assert selected.rejection_counts == {"false_numeric_comparison": 1}
    assert selected.candidates[0]["normative_source_ids"] == ["N1", "N2"]
    assert selected.source_indexes_by_candidate == ((1, 2), (3,))


def test_questions_cover_multiple_disciplines_without_benchmark() -> None:
    """Проверки обоснования решений универсальны для водоснабжения, ЭОМ и автоматизации."""
    for discipline, marker in [
        ("Водоснабжение", "запас воды"),
        ("ЭОМ", "сечения"),
        ("Автоматизация", "обратная связь"),
    ]:
        facts = PageFacts(discipline, "drawing", "", (), (), (), ())
        questions = " ".join(engineering_questions(facts))
        assert marker in questions
        assert "исходные данные" in questions
        assert "777" not in questions and "ПГ1" not in questions


def test_diameter_markers_do_not_reclassify_russian_words() -> None:
    """Фланец и душ не становятся диаметрами только из-за начальных букв."""
    from pdrd_analysis_service.domain.document_context import normalized_value

    assert normalized_value(_fact("фланец")) == ("text", "фланец")
    assert normalized_value(_fact("душ")) == ("text", "душ")


async def test_cross_page_review_survives_finalization_with_all_evidence() -> None:
    """Конкретное подозрение D не теряется и не становится нормативным нарушением."""

    class ReviewVision:
        """Оставляет предметную неоднозначность для проверки инженером."""

        async def generate_json(self, **request):
            """Возвращает одно решение needs_review с конкретной причиной."""
            return SimpleNamespace(
                payload={
                    "decisions": [
                        {
                            "candidate_id": "C1",
                            "status": "needs_review",
                            "reason": "Неясно, совпадают ли режимы.",
                        }
                    ]
                }
            )

    first = _fact(
        "10",
        unit_raw="м",
        visual_regions=[
            {"x_min": 10, "y_min": 20, "x_max": 50, "y_max": 60, "confidence": 0.9}
        ],
    )
    second = replace(first, value_raw="12")
    options = DocumentContextOptions(3, 2, 0.5, 5, 5, 2, 1000)
    findings = await CheckCrossPageConsistency(ReviewVision(), options).execute(
        pages=(
            DocumentPage(1, "", "", (first,)),
            DocumentPage(5, "", "", (second,)),
        )
    )
    assert len(findings) == 1 and findings[0].status == "needs_review"
    assert [item["page"] for item in findings[0].evidence_locations] == [1, 5]
    _, final, _ = await FinalizeFindings(ReviewVision(), 1000, 10, 600, 0.55).execute(
        findings=findings,
        experience_by_finding={},
    )
    assert final[0].finding_id == findings[0].finding_id
    assert final[0].evidence_locations == findings[0].evidence_locations
    assert final[0].status == "needs_review" and final[0].basis_sources == ()
