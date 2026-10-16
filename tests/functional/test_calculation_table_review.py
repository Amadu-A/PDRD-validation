# tests/functional/test_calculation_table_review.py

"""Настоящие PDF-слова ИОС2.2 → геометрия колонок → контроль кандидатов → Finalization."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from pdrd_analysis_service.application.engineering_program import (
    audit_engineering_candidates,
    finalize_arithmetic_candidate,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import FindingDraft
from pdrd_analysis_service.domain.calculation_tables import calculation_table_facts
from pdrd_analysis_service.domain.document_context import LocatedDocumentFact
from pdrd_analysis_service.domain.engineering_assertion import assertion_from_facts

FIXTURE = (
    Path(__file__).parents[1] / "fixtures/engineering/ios2-calculation-page30.json"
)


def source():
    """Загружает исходный текст и слова PDF; человеческих замечаний в фикстуре нет."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def real_report(*, enabled=True):
    """Передаёт те же поля, которые уже получает document-context HTTP-этап."""
    raw = source()
    return document_engineering_report(
        (DocumentPage(30, raw["text"], "", (), tuple(raw["text_words"])),),
        "control-table",
        enabled=enabled,
    )


def candidate(value, unit):
    """Имитирует арифметическое утверждение, но не подставляет ответ программной проверки."""
    return FindingDraft(
        f"model-{value}-{unit}",
        30,
        "table",
        "document_consistency",
        "warning",
        "confirmed",
        "Итог не соответствует расчёту",
        f"{value} {unit}",
        "Проверить итог",
        0.9,
        (),
        "",
        (),
        "",
        origin_assertions=({"origin": "vlm", "claim_type": "arithmetic"},),
    )


class NoVision:
    """Любая дополнительная генерация для перепроверки арифметики запрещена."""

    async def generate_json(self, **request):
        """Проверяет бюджет всей финализации, включая N enrichment."""
        raise AssertionError("Новый VLM-вызов для арифметики")


def test_real_column_header_keeps_emergency_hourly_value_and_empty_day_cell():
    """5,900 находится под м³/ч; тире суточной колонки сохраняется пустым, не нулём."""
    raw = source()
    facts = calculation_table_facts(raw["text_words"], 30)
    assert len(facts) == 48
    emergency = [
        item
        for item in facts
        if item.row_label == "Аварийная подпитка сетевого контура"
    ]
    assert (
        next(item for item in emergency if item.value_raw == "5,900").unit_raw == "м³/ч"
    )
    assert (
        len(
            [
                item
                for item in emergency
                if item.unit_raw == "м³/сут" and item.value_raw == ""
            ]
        )
        == 2
    )
    assert all(
        item.scope_operating_mode == "Аварийное падение давления" for item in emergency
    )
    assert all(
        item.calculation_group == "Эксплуатационные расходы" for item in emergency
    )
    assert all(
        {region["role"] for region in item.source_regions}
        >= {"column_header", "row_header", "cell"}
        for item in emergency
    )


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "value,unit", [("155,122", "м³/сут"), ("42,323", "м³/ч"), ("12,264", "л/с")]
)
async def test_mixed_mode_sum_assertion_cannot_become_confirmed_at_finalization(
    value, unit, enabled
):
    """Эксплуатационные расходы разных режимов не становятся автоматической суммой всех строк."""
    original = candidate(value, unit)
    (guarded,) = audit_engineering_candidates((original,), real_report(enabled=enabled))
    assert guarded.status == "hypothesis"
    assert (
        guarded.origin_assertions[-1]["reason_code"]
        == "mixed_operating_modes_not_additive"
    )
    _, final, metrics = await FinalizeFindings(NoVision(), 2000, 8, 1000, 0.5).execute(
        findings=(guarded,), experience_by_finding={}
    )
    assert final[0].status == "hypothesis" and metrics["vlm_call_count"] == 0
    assert final[0].evidence == original.evidence


@pytest.mark.parametrize(
    "value,unit", [("116,033", "м³/сут"), ("4,835", "м³/ч"), ("1,343", "л/с")]
)
async def test_initial_filling_total_remains_specific_engineer_question(value, unit):
    """Неизвестный смысл итога заполнения не доказывает ни ошибку, ни правильность проекта."""
    original = candidate(value, unit)
    (guarded,) = audit_engineering_candidates((original,), real_report())
    assert guarded.status == "needs_review" and value in guarded.comment
    assert "производительность" in guarded.recommendation_draft
    assert guarded.evidence == original.evidence
    _, final, metrics = await FinalizeFindings(NoVision(), 2000, 8, 1000, 0.5).execute(
        findings=(guarded,), experience_by_finding={}
    )
    assert final[0].status == "needs_review" and metrics["vlm_call_count"] == 0


async def test_wrong_claimed_column_unit_is_refuted_by_original_header_geometry():
    """Даже совпадающее число не позволяет заменить фактическую колонку другой единицей."""
    report = real_report()
    actual = next(
        item.observation
        for item in report.registry.evidence
        if item.observation.fact.value_raw == "5,900"
    )
    wrong = LocatedDocumentFact(
        30, 1, replace(actual.fact, unit_raw="м³/сут", column_binding="")
    )
    assertion = assertion_from_facts((wrong,))
    original = replace(
        candidate("5,900", "м³/сут"), origin_assertions=(assertion.as_origin(),)
    )
    (guarded,) = audit_engineering_candidates((original,), report)
    assert (
        guarded.status == "hypothesis"
        and guarded.origin_assertions[-1]["reason_code"]
        == "column_unit_counterevidence"
    )
    assert finalize_arithmetic_candidate(guarded).status == "hypothesis"
    _, final, metrics = await FinalizeFindings(NoVision(), 2000, 8, 1000, 0.5).execute(
        findings=(guarded,), experience_by_finding={}
    )
    assert final[0].status == "hypothesis" and metrics["vlm_call_count"] == 0


def test_table_fact_metadata_roundtrips_without_changing_source_identity():
    """Расчётная группа, привязка заголовка и исходные координаты проходят JSON без потерь."""
    from pdrd_analysis_service.domain.document_context import document_fact_from_mapping

    fact = real_report().registry.evidence[0].observation.fact
    assert document_fact_from_mapping(json.loads(json.dumps(asdict(fact)))) == fact


@pytest.mark.parametrize(
    "change", ["repeat", "continuation", "gap", "different_columns", "d_off"]
)
def test_only_explicit_compatible_adjacent_continuation_links_table_ids(change):
    """Повторённый заголовок и похожее число не доказывают продолжение предыдущей таблицы."""
    from pdrd_analysis_service.domain.calculation_tables import (
        calculation_tables_by_page,
    )

    raw = source()["text_words"]
    second = [dict(word) for word in raw]
    if change != "repeat":
        second = [
            {**word, "text": "Продолжение таблицы"}
            if word["text"] == "Таблица"
            else word
            for word in second
        ]
    if change == "different_columns":
        second = [
            {**word, "text": "м³/сут"} if word["text"] == "м3/ч" else word
            for word in second
        ]
    page = 32 if change == "gap" else 31
    facts = calculation_tables_by_page(
        ((30, raw), (page, second)), link_continuations=change != "d_off"
    )
    first_ids = {item.table_id for item in facts[30]}
    next_ids = {item.table_id for item in facts[page]}
    if change == "continuation":
        assert first_ids == next_ids and first_ids
        assert all(item.source_item_id.startswith("calc-p31-") for item in facts[page])
    else:
        assert not first_ids & next_ids


def test_unavailable_or_ambiguous_header_never_supplies_guessed_unit():
    """Без исходной геометрии и с дублирующимся заголовком подтверждённая привязка не создаётся."""
    assert calculation_table_facts([], 30) == ()
    words = source()["text_words"]
    ambiguous = [
        {**word, "text": "л/с"} if word["text"] == "м3/ч" else word for word in words
    ]
    assert calculation_table_facts(ambiguous, 30) == ()


@pytest.mark.parametrize(
    "case,property_name,values,unit,claim",
    [
        (
            "H09",
            "Назначение системы",
            ("водоотведение", "водоснабжение"),
            "",
            "contradiction",
        ),
        ("H10", "Протяжённость сети", ("147", "150"), "м", "contradiction"),
        ("H16", "Секундный расход воды", ("0,5205", "0,205"), "л/с", "contradiction"),
        ("H24", "Внутренняя ссылка", ("таблица 1", "таблица 1.1"), "", "documentation"),
    ],
)
def test_existing_expert_claims_are_not_hidden_by_table_arithmetic_gate(
    case, property_name, values, unit, claim
):
    """Проверяет сохранение четырёх уже найденных предметов; не выдаётся за метрику обнаружения."""
    from pdrd_analysis_service.domain.document_context import document_fact_from_mapping

    facts = tuple(
        LocatedDocumentFact(
            page,
            1,
            document_fact_from_mapping(
                {
                    "kind": "parameter",
                    "subject_type": "system",
                    "identifier": "В2",
                    "subject_name": "Система В2",
                    "property_type": "parameter",
                    "property_name": property_name,
                    "value_raw": value,
                    "unit_raw": unit,
                    "evidence_text": f"В2 {property_name}: {value} {unit}",
                    "scope_operating_mode": "",
                    "table_id": f"T{page}" if case == "H16" else "",
                    "relation": "table_cell" if case == "H16" else "",
                    "extraction_method": "pdf_text",
                }
            ),
        )
        for page, value in zip((15, 30), values, strict=True)
    )
    assertion = assertion_from_facts(facts, claim_type=claim, include_related=True)
    finding = replace(
        candidate(values[0], unit),
        finding_id=case,
        origin_assertions=(assertion.as_origin(),),
        comment=f"{property_name}: {values[0]} и {values[1]}",
    )
    (checked,) = audit_engineering_candidates((finding,), real_report())
    assert checked == finding
    assert all(not item.fact.scope_operating_mode for item in assertion.observations)


def test_legacy_total_assertion_uses_native_header_even_with_another_table_id():
    """Старый JSON без нового типа arithmetic тоже получает проверку своего табличного предмета."""
    actual = next(
        item.observation
        for item in real_report().registry.evidence
        if item.observation.fact.value_raw == "116,033"
        and item.observation.fact.relation == "table_total"
    )
    legacy = replace(
        actual, fact=replace(actual.fact, table_id="model-table", column_binding="")
    )
    assertion = assertion_from_facts((legacy,))
    original = replace(
        candidate("116,033", "м³/сут"), origin_assertions=(assertion.as_origin(),)
    )
    (checked,) = audit_engineering_candidates((original,), real_report())
    assert checked.status == "needs_review" and "116,033" in checked.comment


def test_comparison_of_two_independent_totals_is_not_reclassified_as_arithmetic():
    """Два значения одного итога в разных представлениях остаются предметом сопоставимости."""
    totals = tuple(
        item.observation
        for item in real_report().registry.evidence
        if item.observation.fact.relation == "table_total"
    )[:2]
    assertion = assertion_from_facts(totals, include_related=True)
    original = replace(
        candidate("155,122", "м³/сут"), origin_assertions=(assertion.as_origin(),)
    )
    assert audit_engineering_candidates((original,), real_report()) == (original,)


@pytest.mark.parametrize("unit", ["м3/ч", "м³ / ч", "m3/h"])
def test_equivalent_header_spelling_does_not_refute_real_parameter_claim(unit):
    """Пробелы, надстрочная тройка и латинская запись не создают ложное контрдоказательство."""
    actual = next(
        item.observation
        for item in real_report().registry.evidence
        if item.observation.fact.value_raw == "5,900"
    )
    observed = replace(
        actual, fact=replace(actual.fact, unit_raw=unit, column_binding="")
    )
    assertion = assertion_from_facts((observed,))
    original = replace(
        candidate("5,900", unit), origin_assertions=(assertion.as_origin(),)
    )
    assert audit_engineering_candidates((original,), real_report()) == (original,)


def explicit_sum_words(*, wrong=True, end=True, empty=False):
    """Другой документ: только исходные слова, включая правило, режим и конец; флагов готовности нет."""
    words = []

    def row(y, cells):
        for x, text in cells:
            words.append(
                {
                    "text": text,
                    "bbox": {"x_min": x, "y_min": y, "x_max": x + 35, "y_max": y + 10},
                }
            )

    row(20, [(60, "Таблица"), (110, "9")])
    row(100, [(60, "Наименование"), (330, "Режим"), (600, "м3/ч"), (800, "л/с")])
    row(150, [(60, "Потребитель-А"), (330, "Постоянно"), (600, "2.0"), (800, "1.0")])
    row(
        180,
        [
            (60, "Потребитель-Б"),
            (330, "Постоянно"),
            (600, "-" if empty else "3.0"),
            (800, "2.0"),
        ],
    )
    row(
        210,
        [
            (60, "Итого"),
            (120, "сумма"),
            (180, "всех"),
            (230, "строк"),
            (600, "9.0" if wrong else "5.0"),
            (800, "3.0"),
        ],
    )
    if end:
        row(240, [(60, "Конец"), (120, "таблицы")])
    return words


@pytest.mark.parametrize(
    "wrong,end,empty,status",
    [
        (True, True, False, "confirmed"),
        (False, True, False, "no_finding"),
        (True, False, False, "not_evaluable"),
        (True, True, True, "not_evaluable"),
    ],
)
async def test_actual_sum_is_detected_from_source_words_without_ready_proof_flags(
    wrong, end, empty, status
):
    """Проверяет обнаружение настоящей ошибки другого документа на программном пути, без готовых findings."""
    from pdrd_analysis_service.application.engineering_program import (
        program_findings,
        verified_programmatic_check,
    )

    words = explicit_sum_words(wrong=wrong, end=end, empty=empty)
    text = "\n".join(
        " ".join(word["text"] for word in words if word["bbox"]["y_min"] == y)
        for y in sorted({word["bbox"]["y_min"] for word in words})
    )
    report = document_engineering_report(
        (DocumentPage(1, text, "", (), tuple(words)),), "independent-sum"
    )
    checks = [
        check
        for check in report.checks
        if check.rule == "table_sum"
        and check.observations[
            -2 if check.observations[-1].fact.relation == "table_end" else -1
        ].fact.unit_raw
        == "м³/ч"
    ]
    assert checks and checks[0].status == status
    findings = program_findings(report)
    assert len(findings) == int(status == "confirmed")
    if findings:
        assert verified_programmatic_check(findings[0])
        _, final, metrics = await FinalizeFindings(
            NoVision(), 2000, 8, 1000, 0.5
        ).execute(findings=findings, experience_by_finding={})
        assert final[0].status == "confirmed" and metrics["vlm_call_count"] == 0
