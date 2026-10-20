# services/analysis-service/tests/unit/test_engineering_assertions.py

"""Регрессии общего инженерного предмета, способов доказательства и сохранения происхождения."""

import json
from dataclasses import replace

import pytest
from pdrd_analysis_service.application.engineering_plan import (
    bind_candidate_assertion,
    engineering_plan,
)
from pdrd_analysis_service.application.prompts import build_finalization_prompt
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentContextOptions,
    DocumentPage,
    build_page_document_context,
    merge_cross_page_findings,
)
from pdrd_analysis_service.application.use_cases.finalization import (
    FinalizeFindings,
    _safe_generated_text,
)
from pdrd_analysis_service.domain.analysis import DocumentContextSource
from pdrd_analysis_service.domain.document_context import LocatedDocumentFact
from pdrd_analysis_service.domain.engineering_assertion import (
    assertion_from_facts,
    deterministic_unit_mismatch,
    read_assertion,
    same_assertion,
)
from pdrd_analysis_service.domain.engineering_claims import (
    unsupported_engineering_claim,
)
from pdrd_analysis_service.domain.engineering_facts import (
    explicit_design_parameters,
    merge_text_engineering_facts,
)
from pdrd_analysis_service.domain.evidence_audit import audit_assertion

from .test_analysis_use_cases import FakeVisionModel, finding, page_facts
from .test_engineering_review import _fact


def _assertion(**changes):
    """Создаёт одну проверяемую претензию к нейтральному объекту."""
    fact = _fact(
        "водоснабжение",
        "Назначение системы S1: водоснабжение",
        kind="relationship",
        property_type="relationship",
        property_name="Назначение системы",
        **changes,
    )
    return assertion_from_facts((LocatedDocumentFact(8, 1, fact),))


def test_assertion_survives_plan_binding_transport_and_final_prompt():
    """Этапы ссылаются на тот же объект и исходный факт вместо повторного толкования текста."""
    original = _assertion()
    facts = replace(page_facts(), document_facts=(original.observations[0].fact,))
    plan = engineering_plan(facts, "", page_number=8)
    candidate = {
        "object_ref": "S1",
        "comment": "Назначение системы различается",
        "hypothesis_ids": [plan[0]["hypothesis_id"]],
        "claim_type": "contradiction",
    }
    origin = bind_candidate_assertion(plan, candidate, 8, ())
    decoded = read_assertion((json.loads(json.dumps(origin, ensure_ascii=False)),))
    assert decoded.identity == original.identity
    assert decoded.observations[0].fact == original.observations[0].fact
    prompt = build_finalization_prompt(
        findings=(replace(finding(), origin_assertions=(origin,)),),
        experience_by_finding={},
        experience_context_limit=200,
    )
    assert decoded.assertion_id in prompt and "engineering_assertion" in prompt


@pytest.mark.parametrize("linked", [True, False])
@pytest.mark.parametrize(
    ("property_name", "comment"),
    [
        ("Назначение системы", "Назначение системы S1 различается"),
        ("Диаметр", "Диаметр S1 различается"),
        ("Длина", "Длина S1 различается"),
        ("Толщина стенки", "Толщина стенки S1 различается"),
        ("Расход воды", "Расход воды S1 различается"),
        ("Напор", "Напор S1 различается"),
        ("Абсолютная отметка", "Абсолютная отметка S1 различается"),
    ],
)
def test_candidate_binding_preserves_each_recognized_property(
    linked, property_name, comment
):
    """Каждый распознанный предмет сохраняет исходный факт со ссылкой на план и без неё."""
    fact = _fact("192,4", "S1: исходная запись 192,4", property_name=property_name)
    plan = engineering_plan(
        replace(page_facts(), document_facts=(fact,)), "", page_number=10
    )
    candidate = {
        "object_ref": "S1",
        "comment": comment,
        "claim_type": "contradiction",
        "hypothesis_ids": [plan[0]["hypothesis_id"]] if linked else [],
    }
    origin = bind_candidate_assertion(plan, candidate, 10, ())
    assertion = read_assertion((origin,))
    assert assertion is not None
    assert assertion.observations == (LocatedDocumentFact(10, 1, fact),)
    assert assertion.verification == "unverified"


@pytest.mark.parametrize("property_name", ["Длина", "Напор", "Диаметр"])
def test_elevation_candidate_does_not_bind_other_properties(property_name):
    """Даже явная ссылка на гипотезу не подменяет отметку другим параметром того же объекта."""
    fact = _fact("192,4", "S1: исходная запись 192,4", property_name=property_name)
    plan = engineering_plan(
        replace(page_facts(), document_facts=(fact,)), "", page_number=10
    )
    assert (
        bind_candidate_assertion(
            plan,
            {
                "object_ref": "S1",
                "comment": "Абсолютная отметка S1 различается",
                "hypothesis_ids": [plan[0]["hypothesis_id"]],
            },
            10,
            (),
        )
        is None
    )


@pytest.mark.parametrize(
    "difference",
    ["object", "point", "condition", "unknown_point", "same_point"],
)
def test_bound_elevations_require_same_object_point_and_conditions(difference):
    """Привязка не доказывает противоречие разных объектов, точек или условий."""
    first = _fact(
        "192,4",
        "S1 точка У1 рабочий режим: абсолютная отметка 192,4 м",
        property_name="Абсолютная отметка",
        unit_raw="м",
        scope_segment="точка У1",
        scope_operating_mode="рабочий",
    )
    second = replace(
        first,
        value_raw="193,4",
        evidence_text="S1 точка У1 рабочий режим: абсолютная отметка 193,4 м",
    )
    if difference == "object":
        second = replace(
            second,
            identifier="S2",
            evidence_text=second.evidence_text.replace("S1", "S2"),
        )
    elif difference == "point":
        second = replace(
            second,
            scope_segment="точка У2",
            evidence_text=second.evidence_text.replace("У1", "У2"),
        )
    elif difference == "condition":
        second = replace(
            second,
            scope_operating_mode="аварийный",
            evidence_text=second.evidence_text.replace("рабочий", "аварийный"),
        )
    elif difference == "unknown_point":
        first, second = (
            replace(
                item,
                scope_segment="участок У1—У2",
                segment_start="У1",
                segment_end="У2",
                evidence_text=item.evidence_text.replace("точка У1", "участок У1—У2"),
            )
            for item in (first, second)
        )
    facts = replace(page_facts(), document_facts=(first, second))
    plan = engineering_plan(facts, "", page_number=10)
    assert len(plan) == 2
    origin = bind_candidate_assertion(
        plan,
        {
            "object_ref": "S1",
            "comment": "Абсолютные отметки различаются",
            "hypothesis_ids": [item["hypothesis_id"] for item in plan],
            "claim_type": "contradiction",
        },
        10,
        (),
    )
    if difference == "object":
        assert origin is None
        return
    assertion = read_assertion((origin,))
    assert assertion is not None and assertion.verification == "unverified"
    assert tuple(item.fact for item in assertion.observations) == (first, second)
    audit = audit_assertion(
        assertion, page_texts={10: first.evidence_text + "\n" + second.evidence_text}
    )
    if difference == "same_point":
        assert audit.disposition == "supported"
    else:
        assert audit.disposition in {"diagnostic", "question"}


@pytest.mark.parametrize(
    "property_name", ["Температура", "Температура стенки", "Длина"]
)
def test_new_candidate_property_requires_exact_binding_without_a_known_alias(
    monkeypatch, property_name
):
    """Новый предмет классификатора не вызывает исключение и не ослабляет проверку свойства."""
    monkeypatch.setattr(
        "pdrd_analysis_service.application.engineering_plan.candidate_property",
        lambda text: "Температура",
    )
    fact = _fact("20", "S1: исходная запись 20", property_name=property_name)
    plan = engineering_plan(
        replace(page_facts(), document_facts=(fact,)), "", page_number=10
    )
    origin = bind_candidate_assertion(
        plan,
        {
            "object_ref": "S1",
            "comment": "Температура S1 различается",
            "hypothesis_ids": [plan[0]["hypothesis_id"]],
        },
        10,
        (),
    )
    if property_name == "Температура":
        assert read_assertion((origin,)).observations == (
            LocatedDocumentFact(10, 1, fact),
        )
    else:
        assert origin is None


@pytest.mark.parametrize("change", ["property", "type", "condition", "evidence"])
def test_identity_keeps_independent_claims_on_same_object(change):
    """Близкий текст и один объект не объединяют разные претензии или независимые основания."""
    left = _assertion()
    if change == "property":
        right = replace(left, property_key="parameter:diameter")
    elif change == "type":
        right = replace(left, claim_type="missing_justification")
    elif change == "condition":
        right = replace(left, conditions=("аварийный режим",))
    else:
        right = replace(
            left,
            observations=(
                LocatedDocumentFact(
                    9,
                    1,
                    replace(
                        left.observations[0].fact, evidence_text="Иная исходная запись"
                    ),
                ),
            ),
        )
    assert not same_assertion(left, right)


def test_same_parameter_does_not_merge_independent_justification_questions_or_regions():
    """Общий параметр не объединяет вопросы расчёта; одинаковая подпись не склеивает разные области."""
    question = replace(_assertion(), claim_type="missing_justification")
    assert not same_assertion(question, question)
    original = _assertion()
    fact = original.observations[0].fact

    def region_assertion(x):
        """Задаёт отдельную область исходного наблюдения."""
        return replace(
            original,
            observations=(
                LocatedDocumentFact(
                    8,
                    1,
                    replace(
                        fact,
                        visual_regions=(
                            {
                                "x_min": x,
                                "y_min": 10,
                                "x_max": x + 10,
                                "y_max": 30,
                                "confidence": 0.9,
                            },
                        ),
                    ),
                ),
            ),
        )

    first, second = region_assertion(10), region_assertion(100)
    assert not same_assertion(first, second)
    assert first.assertion_id != second.assertion_id


def test_dedup_keeps_all_pages_regions_sources_and_history():
    """Перефразирование одной претензии объединяется без потери доказательств и независимого расчётного вопроса."""
    assertion = _assertion()
    source = DocumentContextSource("D-p0008-f0001", 8, evidence_text="Назначение S1")
    other_source = DocumentContextSource(
        "D-p0009-f0001", 9, evidence_text="Второе представление S1"
    )
    left = replace(
        finding(),
        page=8,
        object_ref="S1",
        origin_assertions=(assertion.as_origin(), {"origin": "discovery"}),
        document_context_source_ids=(source.source_id,),
        document_context_basis_sources=(source,),
        evidence_locations=(
            {"page": 8, "text": "Первая область", "visual_regions": [{"x_min": 3}]},
        ),
    )
    right = replace(
        left,
        finding_id="cross",
        comment="Иная формулировка того же противоречия",
        page=9,
        document_context_source_ids=(other_source.source_id,),
        document_context_basis_sources=(other_source,),
        origin_assertions=(assertion.as_origin(), {"origin": "cross_page_validation"}),
        evidence_locations=({"page": 9, "text": "Вторая область"},),
    )
    independent = replace(
        left,
        finding_id="calculation",
        origin_assertions=(
            replace(assertion, claim_type="missing_justification").as_origin(),
        ),
    )
    result = merge_cross_page_findings(
        page_findings={8: (left, independent)}, cross_findings=(right,)
    )
    assert result[8] == (independent,)
    merged = result[9][0]
    assert {row["page"] for row in merged.evidence_locations} == {8, 9}
    assert merged.evidence_locations[1]["visual_regions"] == [{"x_min": 3}]
    assert set(merged.document_context_source_ids) == {"D-p0008-f0001", "D-p0009-f0001"}
    assert {row["origin"] for row in merged.origin_assertions} >= {
        "discovery",
        "cross_page_validation",
    }


async def test_category_is_not_proof_and_missing_justification_is_not_confirmed():
    """Непроверенная категория проходит смысловой gate; отсутствие обоснования остаётся вопросом."""
    draft = replace(
        finding(),
        category="document_consistency",
        origin_assertions=(_assertion().as_origin(),),
    )
    model = FakeVisionModel(
        [
            {
                "findings": [
                    {
                        "finding_id": draft.finding_id,
                        "decision": "reject",
                        "rejection_reason": "Разные условия",
                        "reason_code": "different_condition",
                    }
                ]
            }
        ]
    )
    _, final, _ = await FinalizeFindings(model, 1000, 4, 500, 0.5).execute(
        findings=(draft,), experience_by_finding={}
    )
    assert not final and model.call_count == 1
    pending = replace(
        draft,
        status="confirmed",
        origin_assertions=(
            replace(_assertion(), claim_type="missing_justification").as_origin(),
        ),
    )
    final = FinalizeFindings._build_final(
        finding=pending,
        item={"comment": pending.comment},
        available_experience=(),
        normative_candidates=(),
        guard_normative_free_text=False,
    )
    assert final.status == "needs_review"


async def test_deterministic_conversion_needs_no_model_but_mixed_modes_do():
    """Одна явная строка проверяется программно; изменение режима запрещает такое доказательство."""
    facts = tuple(
        LocatedDocumentFact(
            1,
            index,
            _fact(
                value,
                f"S1: {value} {unit}",
                unit_raw=unit,
                table_id="T",
                row_label="S1",
                property_name="Расход воды",
                extraction_method="pdf_text",
            ),
        )
        for index, (value, unit) in enumerate((("0,738", "м³/ч"), ("0,5205", "л/с")), 1)
    )
    assertion = assertion_from_facts(facts)
    assert deterministic_unit_mismatch(assertion)
    assert not deterministic_unit_mismatch(
        replace(
            assertion,
            observations=(
                facts[0],
                replace(facts[1], fact=replace(facts[1].fact, identifier="S2")),
            ),
        )
    )
    assert not deterministic_unit_mismatch(
        replace(
            assertion,
            observations=(
                facts[0],
                replace(
                    facts[1],
                    fact=replace(
                        facts[1].fact, column_label="Максимальный секундный расход"
                    ),
                ),
            ),
        )
    )
    assert not deterministic_unit_mismatch(
        replace(
            assertion,
            observations=(
                facts[0],
                replace(
                    facts[1],
                    fact=replace(facts[1].fact, scope_operating_mode="аварийный"),
                ),
            ),
        )
    )
    model = FakeVisionModel([])
    options = DocumentContextOptions(12, 5, 0.4, 20, 8, 5, 1000)
    drafts = await CheckCrossPageConsistency(model, options).execute(
        pages=(
            DocumentPage(
                1,
                "S1: 0,738 м³/ч; S1: 0,5205 л/с",
                "",
                tuple(item.fact for item in facts),
            ),
        )
    )
    assert len(drafts) == 1 and model.call_count == 0
    _, final, _ = await FinalizeFindings(model, 1000, 4, 500, 0.5).execute(
        findings=drafts, experience_by_finding={}
    )
    assert len(final) == 1 and model.call_count == 0
    assert read_assertion(final[0].origin_assertions).verification == "deterministic"


@pytest.mark.parametrize(
    "text,reason",
    [
        (
            "В2 имеет Ду100, тогда как на схеме Ø110.",
            "unverified_diameter_compatibility",
        ),
        (
            "Несовпадение диаметра В2: Ду100 и Ø110.",
            "unverified_diameter_compatibility",
        ),
        (
            "Для В2 указаны разные диаметры: Ду100 и Ø110.",
            "unverified_diameter_compatibility",
        ),
        (
            "Разные значения сейсмичности: ОСР-2015-А 8 баллов, ОСР-2015-С 9 баллов.",
            "incomparable_seismic_map",
        ),
        (
            "В PAGE FACTS scope_location указан как Таблица 1.",
            "extraction_is_not_evidence",
        ),
    ],
)
def test_report_formulations_do_not_bypass_claim_validation(text, reason):
    """Реальные варианты формулировок не обходят проверку оснований."""
    assert unsupported_engineering_claim(text) == reason


def test_incomplete_norm_reference_keeps_specific_subject():
    """Слово СП без номера не заменяет содержательное замечание общим шаблоном."""
    text = "В ссылке на СП отсутствует номер документа для трассировки сети."
    assert (
        _safe_generated_text(generated=text, fallback=text, generic="Общий текст")
        == text
    )


def test_table_continuation_is_available_beyond_old_adjacent_prefix():
    """При табличной проверке продолжение не обрезается до первых 600 символов."""
    continuation = (
        "Служебный текст " * 70 + "Подпитка сетевого контура 17,70 0,738 0,5205"
    )
    pages = (
        DocumentPage(14, "Таблица 1", "", ()),
        DocumentPage(15, continuation, "", ()),
    )
    context = build_page_document_context(
        pages=pages,
        semantic_by_page={},
        options=DocumentContextOptions(12, 5, 0.4, 20, 8, 5, 1000),
    )
    assert "17,70 0,738 0,5205" in context[14][0].text


def test_head_roles_are_not_reinterpreted_by_text_extraction():
    """Заголовок раздела не превращает гарантированный напор в требуемый."""
    facts = explicit_design_parameters(
        "Создание требуемого напора воды. Гарантированный напор В1 — 64,82 м. "
        "Требуемый напор В1 составляет 15 м."
    )
    assert [(fact.property_name, fact.value_raw) for fact in facts] == [
        ("Гарантированный напор", "64,82"),
        ("Требуемый напор", "15"),
    ]


async def test_raw_flow_rows_recover_cross_page_mismatch_without_converting_different_rate_roles():
    """Извлечение получает исходные строки, не готовые нарушения или вручную заданные факты."""
    texts = {
        15: "м3/час л/с\nПодпитка сетевого 17,70 0,738 0,5205\nконтура",
        30: "м3/ч л/с\nПодпитка сетевого\n24 После ВПУ При наличии утечек 17,700 0,738 0,205 (1), (3)",
    }
    pages = tuple(
        DocumentPage(page, text, "", merge_text_engineering_facts((), text, page))
        for page, text in texts.items()
    )
    model = FakeVisionModel(
        [
            {
                "decisions": [
                    {
                        "candidate_id": "C1",
                        "status": "needs_review",
                        "reason": "Разные секундные расходы той же строки",
                        "reason_code": "needs_engineer_review",
                    }
                ]
            }
        ]
    )
    options = DocumentContextOptions(12, 5, 0.4, 20, 8, 5, 1000)
    drafts = await CheckCrossPageConsistency(model, options).execute(pages=pages)
    assert len(drafts) == 1 and model.call_count == 1
    assert {source.page for source in drafts[0].document_context_basis_sources} == {
        15,
        30,
    }
    assert "0,5205" in drafts[0].comment and "0,205" in drafts[0].comment
    _, final, _ = await FinalizeFindings(model, 1000, 4, 500, 0.5).execute(
        findings=drafts, experience_by_finding={}
    )
    assert len(final) == 1 and model.call_count == 1
