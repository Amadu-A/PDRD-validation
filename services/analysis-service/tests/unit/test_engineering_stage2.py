# services/analysis-service/tests/unit/test_engineering_stage2.py

"""Полные формулировки арифметических претензий, сопоставимость величин и смысловой quality gate."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.candidate_trace import candidate_key
from pdrd_analysis_service.application.engineering_plan import (
    candidate_engineering_context,
    engineering_plan,
    quantity_checks,
)
from pdrd_analysis_service.application.prompts import build_finalization_prompt
from pdrd_analysis_service.application.use_cases.document_context import (
    merge_cross_page_findings,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.analysis import DocumentContextSource
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
)
from pdrd_analysis_service.domain.engineering_claims import (
    false_numeric_claim,
    unsupported_engineering_claim,
)
from pdrd_analysis_service.domain.engineering_facts import explicit_design_parameters

from .test_analysis_use_cases import FakeVisionModel, finding, page_facts
from .test_engineering_review import _fact


@pytest.mark.parametrize(
    ("comment", "reason"),
    [
        (
            "Для приготовления ГВС 5,086 м³/ч соответствует 18,3 л/с, поэтому табличное значение 1,413 л/с ошибочно.",
            "false_unit_conversion",
        ),
        (
            "В таблице подпитка котлового контура 0,144 м³/ч должна составлять 0,52 л/с; указанное 0,040 л/с неверно.",
            "false_unit_conversion",
        ),
        (
            "Для подпитки сетевого контура 0,738 м³/ч в пересчёте даёт 0,5205 л/с, что подтверждает таблицу.",
            "false_unit_conversion",
        ),
        (
            "Часовой расход 5,086 м3/час и секундный 1,413 л/с не соответствуют друг другу; исправить строку ГВС.",
            "false_unit_mismatch",
        ),
        (
            "Для подпитки котлового контура 0,144 м³/ч и 0,040 л/с не совпадают; требуется исправить расчёт.",
            "false_unit_mismatch",
        ),
        (
            "Подпитка сетевого контура: 0,738 м³/ч соответствует 0,205 л/с, а в таблице указано 0,5205 л/с.",
            None,
        ),
        ("Приготовление ГВС: 5,086 м³/ч соответствует 1,413 л/с с округлением.", None),
        ("Подпитка котлового контура: 0,144 м³/ч = 0,040 л/с.", None),
        (
            "В таблице записано 0,738 м³/ч = 0,5205 л/с, что ошибочно; корректно 0,205 л/с.",
            None,
        ),
        (
            "Запись «0,738 м³/ч = 0,5205 л/с» ошибочна; исправить секундный расход.",
            None,
        ),
        (
            "Расход «5,086 м³/ч» после пересчёта соответствует 18,3 л/с.",
            "false_unit_conversion",
        ),
        (
            "Регенерация: 8,158 м³/сут при работе 3 часа; 2,410 м³/ч и 0,669 л/с — один режим.",
            None,
        ),
        (
            "Система S1: 2 м³/ч; система S2: 4 л/с. Разные системы требуют отдельных расчётов.",
            None,
        ),
        (
            "Значение 75,45 м меньше 60 м, поэтому напор ошибочно завышен.",
            "false_numeric_comparison",
        ),
    ],
)
def test_full_numeric_claims(comment, reason):
    """Слова об ошибке не отключают арифметику; цитата и режим не подменяют авторский вывод."""
    assert false_numeric_claim(comment) == reason


@pytest.mark.parametrize(
    ("comment", "reason"),
    [
        (
            "В тексте Ду100, на схеме Ø110; это несоответствие диаметра той же трубы.",
            "unverified_diameter_compatibility",
        ),
        (
            "Для трубы Ø110 фланец DN100 не соответствует указанному диаметру.",
            "unverified_diameter_compatibility",
        ),
        (
            "Для трубы Ø110 и арматуры DN100 отсутствует переход, указанный в паспорте изделия.",
            None,
        ),
        ("Проверить расчёты.", "non_specific_claim"),
        (
            "Для насоса P3 с напором 48 м не определён расчётный режим; проверить исходную характеристику.",
            None,
        ),
    ],
)
def test_engineering_claim_has_comparable_basis(comment, reason):
    """Разные способы задания диаметра не доказывают несовместимость изделия."""
    assert unsupported_engineering_claim(comment) == reason


def test_conversion_discovery_respects_rounding_row_and_mode():
    """Ошибку обнаруживают исходные величины; корректный пересчёт, другая строка и режим не создают кандидата."""
    hourly = _fact(
        "0,738",
        unit_raw="м³/ч",
        table_id="balance",
        row_label="Подпитка",
        property_name="Расход воды",
        scope_operating_mode="рабочий",
    )
    seconds = replace(hourly, value_raw="0,5205", unit_raw="л/с")
    assert (
        discover_conflicts(
            (LocatedDocumentFact(3, 1, hourly), LocatedDocumentFact(3, 2, seconds)),
            max_candidates=10,
        )[0].mismatch_type
        == "unit_equivalence"
    )
    for corrected in (
        replace(seconds, value_raw="0,205"),
        replace(seconds, row_label="ГВС"),
        replace(seconds, scope_operating_mode="аварийный"),
    ):
        assert (
            discover_conflicts(
                (
                    LocatedDocumentFact(3, 1, hourly),
                    LocatedDocumentFact(3, 2, corrected),
                ),
                max_candidates=10,
            )
            == ()
        )
    first = replace(hourly, value_raw="5,086")
    second = replace(seconds, value_raw="1,413")
    assert (
        discover_conflicts(
            (LocatedDocumentFact(3, 1, first), LocatedDocumentFact(4, 2, second)),
            max_candidates=10,
        )
        == ()
    )


def test_plan_uses_actual_objects_modes_and_counterevidence():
    """Предметный вопрос содержит исходный параметр и доступное расчётное приложение, но не готовое нарушение."""
    fact = _fact(
        "48",
        identifier="P3",
        property_name="Требуемый напор",
        unit_raw="м",
        scope_operating_mode="рабочий",
        scope_segment="участок A",
    )
    facts = replace(page_facts(), document_facts=(fact,))
    source = DocumentContextSource(
        "D-p0012-calc", 12, subject="P3", evidence_text="Расчёт P3: рабочий режим"
    )
    plan = engineering_plan(facts, "", (source,))
    assert plan[0]["object"] == "P3" and plan[0]["value"] == "48"
    assert plan[0]["scope"]["mode"] == "рабочий"
    assert plan[0]["related_sources"][0]["source_id"] == "D-p0012-calc"
    assert "не утверждай" in plan[0]["question"].casefold()
    assert quantity_checks(facts)[0]["base_unit"] == "м"


async def test_literal_quote_does_not_bypass_review_of_conclusion():
    """Дословный evidence не запрещает финализатору отклонить неподтверждённый вывод."""
    original = replace(
        finding(),
        status="needs_review",
        basis_sources=(),
        normative_source_ids=(),
        comment="Принцип основания не согласован для выбранного участка.",
        origin_assertions=(
            {"origin": "engineering_verification", "evidence_grounded": True},
        ),
    )
    model = FakeVisionModel(
        [
            {
                "findings": [
                    {
                        "finding_id": original.finding_id,
                        "decision": "reject",
                        "rejection_reason": "Цитата относится к другому условию прокладки.",
                    }
                ]
            }
        ]
    )
    _, final, metrics = await FinalizeFindings(model, 1000, 10, 600, 0.55).execute(
        findings=(original,), experience_by_finding={}
    )
    assert final == () and model.call_count == 1
    assert metrics["rejected_count"] == 1 and metrics["deterministic_review_count"] == 0


async def test_false_arithmetic_cannot_pass_finalization_even_with_grounded_quote():
    """Кандидат с доказуемо неверным пересчётом отклоняется до VLM с фиксированной причиной."""
    original = replace(
        finding(),
        comment="5,086 м³/ч соответствует 18,3 л/с, значение таблицы ошибочно.",
    )
    model = FakeVisionModel([])
    _, final, metrics = await FinalizeFindings(model, 1000, 10, 600, 0.55).execute(
        findings=(original,), experience_by_finding={}
    )
    assert final == () and model.call_count == 0
    assert metrics["rejected_findings"][0]["reason"] == "false_unit_conversion"


def test_adopted_flow_creates_specific_question_without_object_tag():
    """Принятый расход из текста не теряется при отсутствии обозначения и не становится готовым нарушением."""
    text = "Расчетный расход на наружное пожаротушение принят 10 л/с. Расчёт приведён в приложении."
    parameters = explicit_design_parameters(text)
    assert (
        len(parameters) == 1
        and parameters[0].value_raw == "10"
        and parameters[0].unit_raw == "л/с"
    )
    plan = engineering_plan(page_facts(), text)
    assert len(plan) == 1 and plan[0]["value"] == "10"
    assert "пожаротушение" in plan[0]["object"]
    assert "Где обосновано" in plan[0]["question"]
    assert "принят 10 л/с" in plan[0]["evidence"]


def test_finalization_receives_object_modes_without_neighbouring_tag():
    """Финализатор видит рабочий и аварийный режим S7, но не подменяет их параметрами S70."""
    working = _fact("48", identifier="S7", unit_raw="м", scope_operating_mode="рабочий")
    emergency = replace(working, value_raw="55", scope_operating_mode="аварийный")
    other = replace(working, identifier="S70", value_raw="70")
    facts = replace(page_facts(), document_facts=(working, emergency, other))
    context = candidate_engineering_context(facts, {"object_ref": "S7"}, 3)
    assert len(context["facts"]) == 2
    original = replace(
        finding(),
        object_ref="S7",
        origin_assertions=({"origin": "engineering_context", "context": context},),
    )
    prompt = build_finalization_prompt(
        findings=(original,), experience_by_finding={}, experience_context_limit=600
    )
    assert '"scope_operating_mode":"рабочий"' in prompt
    assert '"scope_operating_mode":"аварийный"' in prompt
    assert '"identifier":"S70"' not in prompt


async def test_compact_understanding_retains_graphical_targets_without_d():
    """Основной запрос сохраняет целевое чтение без D; незаданная область не вызывает crop."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    target = {
        "object_ref": "S7",
        "kind": "connection",
        "question": "Куда приходит линия?",
        "observation": "Линия S7",
        "regions": [],
    }
    model = FakeVisionModel(
        [
            {
                "discipline": "ВК",
                "page_type": "drawing",
                "summary": "Схема",
                "objects": [],
                "connections": [],
                "labels": [],
                "normative_queries": [],
                "graphical_targets": [target],
            },
        ]
    )
    facts, metrics = await UnderstandPage(
        model,
        1500,
        image_cropper=SimpleNamespace(crop=AsyncMock()),
        max_graphical_crops=1,
    ).execute(
        page_number=7,
        heuristic_page_type="drawing",
        extracted_text="S7",
        image_bytes=b"render",
        use_document_context=False,
    )
    assert facts.graphical_targets == (target,) and not facts.document_facts
    assert model.call_count == 1
    assert metrics.as_dict()["understanding"]["recovery_count"] == 0


def test_trace_identity_distinguishes_same_comment_on_other_page():
    """Одинаковые текст и тег на разных физических страницах не смешиваются в диагностике."""
    candidate = {"object_ref": "S7", "comment": "Уточнить режим S7.", "evidence": "S7"}
    assert candidate_key(candidate, page=3) != candidate_key(candidate, page=4)
    assert candidate_key(candidate, page=3) == candidate_key(dict(candidate), page=3)


@pytest.mark.parametrize(
    ("count", "mode", "candidate_count"),
    [
        (1, "проектируемое", 1),
        (2, "проектируемое", 0),
        (5, "проектируемое", 0),
        (1, "существующее", 0),
    ],
)
def test_inventory_uses_unique_explicit_members_and_project_scope(
    count, mode, candidate_count
):
    """Проверяет избыточный состав без ложной недостачи, двойного учёта и смешения статусов."""
    declared = _fact(
        str(count),
        kind="quantity",
        subject_type="hydrant",
        identifier="S7",
        property_type="inventory_count",
        property_name="Количество",
        scope_operating_mode="проектируемое",
    )
    first = _fact(
        "S7",
        kind="relationship",
        subject_type="hydrant",
        identifier="G1",
        relation="member_of",
        scope_system="S7",
        scope_operating_mode=mode,
    )
    second = replace(first, identifier="G2")
    located = (
        LocatedDocumentFact(1, 1, declared),
        LocatedDocumentFact(2, 1, first),
        LocatedDocumentFact(2, 2, second),
        LocatedDocumentFact(3, 1, first),
    )
    candidates = discover_conflicts(located, max_candidates=10)
    assert len(candidates) == candidate_count
    if candidates:
        assert candidates[0].mismatch_type == "inventory_composition"
        from pdrd_analysis_service.application.use_cases.document_context import (
            _candidate_payload,
        )

        payload = _candidate_payload(1, candidates[0], 8)
        assert payload["observed_unique_count"] == 2
        assert {fact["identifier"] for fact in payload["facts"]} == {"S7", "G1", "G2"}


@pytest.mark.parametrize(
    ("second_number", "second_page", "second_code", "candidate_count"),
    [
        (3, 12, "DOC-A", 0),
        (4, 12, "DOC-A", 1),
        (2, 12, "DOC-A", 1),
        (4, 15, "DOC-A", 0),
        (1, 12, "DOC-B", 0),
    ],
)
def test_printed_sheet_sequence_is_not_physical_pdf_number(
    second_number, second_page, second_code, candidate_count
):
    """Проверяет печатные номера одного шифра без фиктивных пропусков выбранного диапазона."""
    first = _fact(
        "2",
        subject_type="drawing_document",
        identifier="DOC-A",
        property_type="sheet_number",
        property_name="Лист",
    )
    second = replace(first, value_raw=str(second_number), identifier=second_code)
    candidates = discover_conflicts(
        (
            LocatedDocumentFact(11, 1, first),
            LocatedDocumentFact(second_page, 1, second),
        ),
        max_candidates=10,
    )
    assert len(candidates) == candidate_count
    if candidates:
        assert candidates[0].mismatch_type == "sheet_sequence"


@pytest.mark.parametrize("same_issue", [False, True])
def test_cross_page_dedup_does_not_merge_independent_claims_on_one_source(same_issue):
    """Общий D-источник не доказывает тождество замечаний; дословный повтор одного объекта объединяется."""
    source = DocumentContextSource(
        "D-p0012-f0001", 12, evidence_text="Исходный факт S7"
    )
    first = replace(
        finding(),
        page=12,
        category="document_consistency",
        object_ref="S7",
        document_context_source_ids=(source.source_id,),
        document_context_basis_sources=(source,),
    )
    second = replace(
        first,
        finding_id="another",
        comment=first.comment if same_issue else "Иной вопрос расчётного режима S7.",
    )
    merged = merge_cross_page_findings(
        page_findings={12: (first,)}, cross_findings=(second,)
    )
    assert len(merged[12]) == (1 if same_issue else 2)


def test_repeated_inventory_statement_keeps_both_sources_in_one_candidate():
    """Повтор одного заявленного количества не создаёт второе замечание и не теряет доказательство."""
    count = _fact(
        "1",
        kind="quantity",
        subject_type="device",
        identifier="S7",
        property_type="inventory_count",
    )
    first = _fact(
        "S7",
        identifier="G1",
        subject_type="device",
        relation="member_of",
        scope_system="S7",
    )
    second = replace(first, identifier="G2")
    located = (
        LocatedDocumentFact(1, 1, count),
        LocatedDocumentFact(2, 1, count),
        LocatedDocumentFact(3, 1, first),
        LocatedDocumentFact(3, 2, second),
    )
    candidates = discover_conflicts(located, max_candidates=10)
    assert len(candidates) == 1 and len(candidates[0].facts) == 4


def test_relationship_and_sheet_questions_do_not_demand_calculations():
    """Связь элемента и печатный номер проверяются по назначению, а не по отсутствующему расчёту числа."""
    member = _fact("S7", identifier="G1", relation="member_of", kind="relationship")
    sheet = _fact(
        "2",
        subject_type="drawing_document",
        identifier="DOC-A",
        property_type="sheet_number",
    )
    plan = engineering_plan(replace(page_facts(), document_facts=(member, sheet)), "")
    assert "принадлежность" in plan[0]["question"]
    assert "печатный номер" in plan[1]["question"]
    assert all("Где обосновано" not in item["question"] for item in plan)
