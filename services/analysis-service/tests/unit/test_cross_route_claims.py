# services/analysis-service/tests/unit/test_cross_route_claims.py

"""Единая инженерная претензия сохраняет разные маршруты и независимые доказательства."""

from dataclasses import replace

from pdrd_analysis_service.application.use_cases.document_context import (
    merge_cross_page_findings,
)
from pdrd_analysis_service.domain.analysis import FindingDraft
from pdrd_analysis_service.domain.claim_identity import same_engineering_claim
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_assertion import assertion_from_facts


def finding(
    identifier,
    *,
    prop="Расход воды",
    claim_type="contradiction",
    mode="",
    second="0,205",
):
    """Задаёт исходные значения подпитки; ожидаемый вывод не встраивается в модель."""
    observations = []
    for page, value in ((15, "0,5205"), (20, second)):
        fact = document_fact_from_mapping(
            {
                "kind": "parameter",
                "subject_type": "flow_consumer",
                "identifier": "Подпитка сетевого контура",
                "property_type": "parameter",
                "property_name": prop,
                "value_raw": value,
                "unit_raw": "л/с",
                "scope_operating_mode": mode,
                "evidence_text": f"Подпитка сетевого контура {value} л/с",
            }
        )
        observations.append(LocatedDocumentFact(page, 1, fact))
    assertion = assertion_from_facts(tuple(observations), claim_type=claim_type)
    return FindingDraft(
        identifier,
        15,
        "table",
        "document_consistency",
        "warning",
        "needs_review",
        "Согласовать две записи расхода подпитки",
        observations[0].fact.evidence_text,
        "Уточнить принятое значение",
        0.8,
        (),
        "",
        (),
        "",
        origin_assertions=(assertion.as_origin(),),
    )


def test_same_claim_merges_values_and_preserves_all_route_history():
    """Синонимичные свойства при одном доказательстве объединяются до Finalization."""
    a = finding("route-A", prop="Расход воды")
    b = finding("route-B", prop="Расчётный расход воды")
    assert same_engineering_claim(a, b)
    merged = merge_cross_page_findings(page_findings={15: (a, b)}, cross_findings=())
    assert len(merged[15]) == 1
    assert any(
        origin.get("finding_id") == "route-A"
        for origin in merged[15][0].origin_assertions
    )


def test_same_object_different_property_claim_type_values_or_conditions_remain_separate():
    """Нельзя поглощать маркировку, расчётный вопрос или другой режим одним расходом."""
    a = finding("A")
    for b in (
        finding("B", prop="Напор"),
        finding("B", claim_type="missing_justification"),
        finding("B", mode="аварийный"),
        finding("B", second="0,3"),
    ):
        assert not same_engineering_claim(a, b)
        merged = merge_cross_page_findings(
            page_findings={15: (a, b)}, cross_findings=()
        )
        assert len(merged[15]) == 2


def test_equipment_and_document_claims_merge_only_with_full_applicability():
    """EQ сохраняет паспортные доказательства; неизвестная применимость не поглощает D."""
    document = finding("D")
    equipment = replace(
        document,
        finding_id="EQ",
        category="equipment",
        origin_assertions=(),
        equipment_documentation_source_ids=("EQ-source",),
        equipment_details={
            "object_ref": "Подпитка сетевого контура",
            "property_name": "flow_rate",
            "project_value": "0,5205",
            "project_unit": "л/с",
            "manufacturer_value": "0,205",
            "manufacturer_unit": "л/с",
            "applicability_status": "applicable",
        },
    )
    assert same_engineering_claim(document, equipment)
    result = merge_cross_page_findings(
        page_findings={15: (document, equipment)}, cross_findings=()
    )
    assert len(result[15]) == 1
    assert result[15][0].equipment_documentation_source_ids == ("EQ-source",)
    assert not same_engineering_claim(
        document,
        replace(
            equipment,
            equipment_details={
                **equipment.equipment_details,
                "applicability_status": "not_proven",
            },
        ),
    )
