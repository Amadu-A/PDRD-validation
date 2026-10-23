# services/analysis-service/tests/unit/test_quality_stage3_reliability.py

"""Достоверность сравнения, полные строки и сохранение доказательств этапа 3."""

import json
from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    verified_programmatic_check,
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
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.analysis import FindingDraft, GenerationResult
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    assertion_from_facts,
    read_assertion,
)
from pdrd_analysis_service.domain.engineering_facts import merge_text_engineering_facts
from pdrd_analysis_service.domain.evidence_audit import audit_assertion
from pdrd_analysis_service.domain.visual_specification import visual_specification_facts
from pdrd_analysis_service.transport.http.schemas import PageFactsPayload

from .test_specification_page_facts import metrics, model


async def test_vllm_budget_error_transfers_only_closed_specification_fields(
    monkeypatch,
):
    """Реальный адаптер передаёт закрытые строки в штатное продолжение, без повтора GPU."""
    from pdrd_analysis_service.application.ports.vision_model import (
        VisionModelOutputBudgetError,
    )
    from pdrd_analysis_service.application.understanding_contract import (
        build_understanding_contract,
    )

    from .test_vllm_adapter import (
        FakeAsyncClient,
        _completion,
        _install_fake_client,
        _model,
    )

    rows = graphic_rows()
    content = '{"specification_rows":' + json.dumps(rows) + ',"document_facts":[{'
    _install_fake_client(
        monkeypatch,
        post_responses=[_completion(content=content, finish_reason="length")],
    )
    prompt, schema = build_understanding_contract(
        page_number=26,
        heuristic_page_type="unknown",
        extracted_text="",
        max_equipment=40,
    )
    with pytest.raises(VisionModelOutputBudgetError) as error:
        await _model().generate_json(
            prompt=prompt,
            schema=schema,
            num_predict=4000,
            seed=100,
            stage="page_understanding:26",
            image_bytes=b"image",
        )
    assert error.value.completed_fields == {"specification_rows": rows}
    assert len(FakeAsyncClient.requests) == 1


@pytest.mark.parametrize(
    "tail", ['"equipment_identities":[{"model":"', '"document_facts":[{']
)
async def test_completed_rows_survive_output_limit_without_repair_or_second_call(tail):
    """Закрытый массив сохраняется; обрезанный соседний массив не дополняется и не публикуется."""
    from pdrd_analysis_service.application.ports.vision_model import (
        VisionModelOutputBudgetError,
    )
    from pdrd_analysis_service.infrastructure.vllm import completed_specification_fields

    rows = graphic_rows()
    content = (
        '{"specification_rows":' + json.dumps(rows, ensure_ascii=False) + "," + tail
    )
    completed = completed_specification_fields(content)
    assert completed == {"specification_rows": rows}
    provider = model()
    provider.generate_json.side_effect = VisionModelOutputBudgetError(
        "output_limit", metrics=metrics(), completed_fields=completed
    )
    facts, result = await UnderstandPage(provider, 4000).execute(
        page_number=26,
        heuristic_page_type="unknown",
        extracted_text="1 2 3",
        image_bytes=b"image",
        use_document_context=True,
        use_equipment_web_search=True,
    )
    assert provider.generate_json.await_count == 1
    assert facts.visual_specification_rows == tuple(rows)
    assert len(facts.document_facts) == 10
    assert result.as_dict()["understanding"]["result"] == "incomplete"
    assert result.as_dict()["understanding"]["limited_reason"] == "output_budget"
    assert result.as_dict()["understanding"]["visual_rows_accepted"] == 2
    assert completed_specification_fields(content[: content.index('"rows"') + 8]) == {}


async def test_invalid_visual_rows_remain_in_snapshot_with_incomplete_diagnostic():
    """Отклонённая область не исчезает молча и не маскируется успешным извлечением."""
    provider, rows = model(), graphic_rows()
    rows[0]["rows"][0]["bbox"] = [20, 10, 5, 30]
    provider.generate_json.side_effect = None
    provider.generate_json.return_value = GenerationResult(
        {
            "discipline": "",
            "page_type": "table",
            "summary": "",
            "objects": [],
            "connections": [],
            "labels": [],
            "normative_queries": [],
            "document_facts": [],
            "specification_rows": rows,
        },
        metrics(),
    )
    facts, result = await UnderstandPage(provider, 4000).execute(
        page_number=26,
        heuristic_page_type="unknown",
        extracted_text="",
        image_bytes=b"image",
        use_document_context=True,
    )
    assert facts.visual_specification_rows == tuple(rows)
    diagnostic = result.as_dict()["understanding"]
    assert diagnostic["visual_rows"] == 2 and diagnostic["visual_rows_unresolved"] == 1
    assert diagnostic["result"] == "incomplete"


async def test_specification_without_rows_is_incomplete_even_when_provider_stops_normally():
    """Отсутствие строк в полном JSON не выдаётся за успешное чтение спецификации."""
    provider = model()
    provider.generate_json.side_effect = None
    provider.generate_json.return_value = GenerationResult(
        {
            "discipline": "",
            "page_type": "Спецификация материалов",
            "summary": "",
            "objects": [],
            "connections": [],
            "labels": [],
            "normative_queries": [],
            "document_facts": [],
            "specification_notes": [
                {"text": "Примечание", "bbox": [10, 800, 500, 900]}
            ],
        },
        metrics(),
    )
    facts, result = await UnderstandPage(provider, 4000).execute(
        page_number=26,
        heuristic_page_type="unknown",
        extracted_text="",
        image_bytes=b"image",
        use_document_context=True,
    )
    assert len(facts.visual_specification_notes) == 1
    assert (
        result.as_dict()["understanding"]["limited_reason"]
        == "specification_rows_not_extracted"
    )


def graphic_rows():
    """Независимые строки поставки двух систем; готовых замечаний во входе нет."""
    return [
        {
            "system": system,
            "header": f"Водопровод {system}",
            "bbox": [50, y - 20, 800, y - 5],
            "rows": [
                {
                    "position": pos,
                    "name": "Труба ПЭ100 SDR11 110×10",
                    "quantity": quantity,
                    "unit": "м",
                    "reference": "ГОСТ Р 70628.1-2023",
                    "bbox": [50, y, 800, y + 20],
                }
            ],
        }
        for system, pos, quantity, y in (
            ("В1", "1", "90", 200),
            ("В2", "6", "150", 400),
        )
    ]


def finding(comment, observations=(), **extra):
    """Создаёт только входной кандидат, а не ожидаемое подтверждение."""
    assertion = assertion_from_facts(observations, include_related=True)
    return FindingDraft(
        finding_id="p25-f1",
        page=25,
        page_type="scheme",
        category="document_consistency",
        severity="warning",
        status="confirmed",
        comment=comment,
        evidence=comment,
        recommendation_draft="Сверить",
        confidence=0.9,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query="",
        object_ref="В2",
        origin_assertions=(
            {"origin": "vlm", "candidate_id": "raw-1", "claim_type": "contradiction"},
            *((assertion.as_origin(),) if assertion else ()),
        ),
        **extra,
    )


def measure(value, page, **extra):
    """Задаёт независимое исходное измерение с явными границами участка."""
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "subject_type": "system",
            "identifier": "В2",
            "property_type": "parameter",
            "property_name": "Диаметр",
            "value_raw": value,
            "unit_raw": "мм",
            "scope_segment": "участок У1-У2",
            "evidence_text": f"В2 участок У1-У2 диаметр {value} мм",
            **extra,
        }
    )
    return LocatedDocumentFact(page, 1, fact)


def test_visual_rows_preserve_system_quantity_size_source_and_original_columns():
    """Количество, материал, стенка и SDR не переезжают в соседнюю систему."""
    rows = graphic_rows()
    original = deepcopy(rows)
    facts = visual_specification_facts(rows, 26)
    quantities = [fact for fact in facts if fact.value_role == "supply_length"]
    assert [(fact.identifier, fact.value_raw) for fact in quantities] == [
        ("В1", "90"),
        ("В2", "150"),
    ]
    assert all(
        fact.source_pages == (26,) and fact.source_row_ids and fact.visual_regions
        for fact in facts
    )
    assert {fact.property_name for fact in facts} >= {
        "Толщина стенки",
        "Наружный диаметр",
        "SDR",
        "Материал",
    }
    for fact in facts:
        assert document_fact_from_mapping(asdict(fact)) == fact
        raw = fact.source_regions[0]["row"]
        assert raw["columns"]["reference"] == "ГОСТ Р 70628.1-2023"
    assert rows == original
    assert len(visual_specification_facts(rows + rows, 26)) == len(facts)


@pytest.mark.parametrize("change", ["system", "row_bbox", "header_bbox", "quantity"])
def test_unbound_or_incomplete_visual_row_is_not_an_engineering_fact(change):
    """Нечитаемые и несвязанные поля не превращаются в уверенные наблюдения."""
    rows = graphic_rows()[:1]
    if change == "system":
        rows[0]["system"] = "В2"
    elif change == "header_bbox":
        rows[0]["bbox"] = [20, 10, 5, 100]
    elif change == "row_bbox":
        rows[0]["rows"][0]["bbox"] = [20, 10, 5, 100]
    else:
        rows[0]["rows"][0]["quantity"] = ""
    assert visual_specification_facts(rows, 26) == ()


@pytest.mark.parametrize("enabled,eq", [(True, True), (True, False), (False, True)])
async def test_visual_rows_use_same_understanding_request_and_roundtrip(enabled, eq):
    """Лимит свободных фактов не обрезает строки; D и EQ берутся из одного чтения."""
    provider = model()
    rows = graphic_rows()
    payload = {
        "discipline": "ВК",
        "page_type": "specification",
        "summary": "Спецификация",
        "objects": [],
        "labels": [],
        "connections": [],
        "normative_queries": [],
        "document_facts": [],
        "specification_rows": rows,
    }
    provider.generate_json.side_effect = None
    provider.generate_json.return_value = GenerationResult(payload, metrics())
    facts, _ = await UnderstandPage(provider, 4000, max_facts_per_page=1).execute(
        page_number=26,
        heuristic_page_type="unknown",
        extracted_text="1 2 3",
        image_bytes=b"image",
        use_document_context=enabled,
        use_equipment_web_search=eq,
    )
    assert provider.generate_json.await_count == 1
    assert provider.generate_json.await_args.kwargs["num_predict"] == 4000
    assert len(facts.document_facts) == (10 if enabled else 0)
    assert len(facts.equipment_identities) == (2 if eq else 0)
    assert facts.visual_specification_rows == tuple(rows)
    assert PageFactsPayload.model_validate(asdict(facts)).to_domain() == facts


@pytest.mark.parametrize("latin_tag", [False, True])
def test_network_length_and_supply_form_specific_question_without_claiming_error(
    latin_tag,
):
    """Нативная протяжённость и графическое количество не приравниваются автоматически."""
    text = "Протяженность сети водоснабжения В2 диаметром Ду 100 мм – 147,00 м."
    rows = graphic_rows()
    if latin_tag:
        rows = json.loads(json.dumps(rows, ensure_ascii=False).replace("В2", "B2"))
    pages = (
        DocumentPage(13, text, "", merge_text_engineering_facts((), text, 13)),
        DocumentPage(26, "", "", visual_specification_facts(rows, 26)),
    )
    report = document_engineering_report(pages, "supply")
    check = next(
        check for check in report.checks if check.rule == "network_supply_scope"
    )
    assert check.status == "needs_review"
    assert ("system_tag_reading" in check.missing_data) is latin_tag
    assert {item.fact.value_raw for item in check.observations} == {"147,00", "150"}
    draft = next(
        f for f in program_findings(report) if "количество поставляемой" in f.comment
    )
    assert draft.status == "needs_review" and "запас" in draft.comment
    assert verified_programmatic_check(draft)
    assert {loc["page"] for loc in draft.evidence_locations} == {13, 26}


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("DN100", "Ø110", "different_parameter_definitions"),
        ("Ду110–150", "DN150", "compatible_parameter_range"),
        ("DN150", "Ду110-150", "compatible_parameter_range"),
    ],
)
def test_diameter_meaning_and_inclusive_range_are_checked_before_confirmation(
    a, b, expected
):
    """Взаимообратный порядок не меняет результат предметного контроля."""
    items = (measure(a, 25), measure(b, 26))
    audit = audit_assertion(
        assertion_from_facts(items, include_related=True),
        page_texts={item.page: item.fact.evidence_text for item in items},
    )
    assert audit.reason_code == expected and audit.disposition == "diagnostic"


def test_ring_main_is_distinct_from_service_entry_and_real_wall_conflict_survives():
    """Различие элементов исключает сравнение; одинаковые границы сохраняют стенку."""
    items = (
        measure("100", 8, evidence_text="В2 кольцевая сеть диаметр DN100"),
        measure("200", 9, evidence_text="В2 проектируемый ввод диаметр DN200"),
    )
    result = audit_assertion(
        assertion_from_facts(items, include_related=True),
        page_texts={item.page: item.fact.evidence_text for item in items},
    )
    assert result.reason_code == "different_object_kinds"
    wall = tuple(
        measure(
            value,
            page,
            property_name="Толщина стенки",
            evidence_text=f"В2 участок У1-У2 толщина стенки {value} мм",
        )
        for page, value in ((25, "10"), (26, "6,6"))
    )
    assert (
        audit_assertion(
            assertion_from_facts(wall, include_related=True),
            page_texts={item.page: item.fact.evidence_text for item in wall},
        ).disposition
        == "supported"
    )
    other = replace(
        wall[1],
        fact=replace(
            wall[1].fact,
            scope_segment="участок У3-У4",
            evidence_text="В2 участок У3-У4 толщина стенки 6,6 мм",
        ),
    )
    assert (
        audit_assertion(
            assertion_from_facts((wall[0], other), include_related=True),
            page_texts={25: wall[0].fact.evidence_text, 26: other.fact.evidence_text},
        ).disposition
        == "diagnostic"
    )


@pytest.mark.parametrize(
    "comment",
    [
        "На схеме В1 Ø110х6,6, в спецификации В2 (вероятно и В1) 110*10 — несоответствие.",
        "На схеме В1 Ø110х10, в спецификации 110*6,6 или наоборот — расхождение.",
        "На схеме В1 Ø110х6,6, однако в спецификации для участка 110*10 — несоответствие.",
        "На схеме В1 Ø110х10, однако в спецификации для участка 110*6,6 — несоответствие.",
        "В ПЗ диаметр В1 Ду100, на схеме Ø110, что соответствует Ду110: расхождение.",
        "В основной надписи номер листа 12 противоречит номеру листа 16 в верхнем углу.",
    ],
)
def test_run12_unbound_comparisons_are_diagnostic_not_user_findings(comment):
    """Отрицательные №9,15,18,19,21,22 не получают подтверждение без исходных сторон."""
    draft = audit_findings_evidence(
        (finding(comment),), page_texts={25: comment}, stage="test"
    )[0]
    assert draft.status == "hypothesis"
    assert evidence_audit(draft)["reason_code"] == "comparison_subject_not_established"
    assert evidence_audit(draft)["status"] == "incomplete"


def test_h09_deduplicates_legacy_context_without_merging_other_claims():
    """Все страницы назначения сохраняются, отдельная претензия к диаметру остаётся."""
    texts = {8: "водоснабжения (В2)", 13: "водоотведения В2", 14: "водоснабжения В2"}
    observations = tuple(
        LocatedDocumentFact(page, 1, merge_text_engineering_facts((), text, page)[0])
        for page, text in texts.items()
    )
    full = finding(
        "Система В2 названа водоотведением",
        observations,
        document_context_basis_sources=(),
    )
    part = replace(
        finding("В2: противоречие назначения системы", observations[1:]),
        finding_id="p13-f1",
        page=13,
    )
    # Представление в таблице не вводит отдельный режим назначения системы.
    part_assertion = read_assertion(part.origin_assertions)
    part = replace(
        part,
        origin_assertions=(
            replace(
                part_assertion,
                observations=tuple(
                    replace(
                        item,
                        fact=replace(
                            item.fact, table_id="table-legend", row_label="В2"
                        ),
                    )
                    for item in part_assertion.observations
                ),
            ).as_origin(),
        ),
    )
    other = replace(finding("Недостаточно обоснован диаметр В2"), finding_id="diameter")
    merged = merge_cross_page_findings(
        page_findings={13: (part, other)}, cross_findings=(full,)
    )
    assert sum(len(values) for values in merged.values()) == 2
    combined = next(
        f for values in merged.values() for f in values if f.finding_id != "diameter"
    )
    assert {
        item.page for item in read_assertion(combined.origin_assertions).observations
    } == {8, 13, 14}


def test_volume_code_hierarchy_does_not_refute_real_explicit_volume_conflict():
    """Шифр не подменяет номер тома, но два явно записанных номера остаются проверяемыми."""
    from pdrd_analysis_service.domain.structural_evidence import (
        structural_counterevidence,
    )

    comment = (
        "Номер тома 5.2.2.2 не соответствует номеру тома 5.2.2 в обозначении документа."
    )
    assert (
        structural_counterevidence(comment, "", {1: "Том 5.2.2.2; ИОС2.2.2"})
        == "document_code_is_not_volume_number"
    )
    assert (
        structural_counterevidence(comment, "", {1: "Том 5.2.2.2", 2: "Том 5.2.2"})
        is None
    )
