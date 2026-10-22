# services/analysis-service/tests/unit/test_quality_stage2_comparability.py

"""Предметная сопоставимость, исключающие назначения и полный путь кандидата."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentPage,
    merge_cross_page_findings,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
    document_fact_from_mapping,
)
from pdrd_analysis_service.domain.engineering_assertion import assertion_from_facts
from pdrd_analysis_service.domain.evidence_audit import (
    audit_assertion,
)

from .test_document_context import _options


def observation(value, page=1, **extra):
    """Задаёт исходное утверждение, а не готовое замечание."""
    return LocatedDocumentFact(
        page,
        1,
        document_fact_from_mapping(
            {
                "kind": "relationship",
                "subject_type": "system",
                "identifier": "S7",
                "property_type": "relationship",
                "property_name": "Назначение системы",
                "value_raw": value,
                "evidence_text": f"Система S7 {value}",
                **extra,
            }
        ),
    )


class EmptyModel:
    """Не подсказывает заключений: пустой ответ проверяет программный маршрут."""

    def __init__(self):
        """Считает обращения к генерации."""
        self.calls = 0

    async def generate_json(self, **request):
        """Возвращает структурированный пропуск решения."""
        self.calls += 1
        return SimpleNamespace(payload={"decisions": []})


@pytest.mark.parametrize("tag", ["S7", "В2"])
async def test_purpose_survives_empty_model_merge_and_finalization(tag):
    """Прямой конфликт назначений не зависит от свободного решения VLM и не теряет области."""
    regions = ({"x_min": 10, "y_min": 20, "x_max": 80, "y_max": 90, "confidence": 1.0},)
    pages = tuple(
        DocumentPage(
            page,
            f"Система {tag}: {value}",
            "",
            (
                document_fact_from_mapping(
                    {
                        "kind": "relationship",
                        "subject_type": kind,
                        "identifier": tag,
                        "property_type": "relationship",
                        "property_name": "Назначение системы",
                        "value_raw": value,
                        "evidence_text": f"Система {tag}: {value}",
                        "visual_regions": regions,
                        "source_pages": [page],
                    }
                ),
            ),
        )
        for page, kind, value in (
            (13, "network", "водоотведение"),
            (14, "system", "водоснабжение"),
        )
    )
    model, diagnostics = EmptyModel(), {}
    drafts = await CheckCrossPageConsistency(model, _options()).execute(
        pages=pages, diagnostics=diagnostics
    )
    assert len(drafts) == 1 and drafts[0].status == "confirmed"
    assert diagnostics["candidates"][0]["disposition"] == "saved"
    merged = merge_cross_page_findings(
        page_findings={13: drafts}, cross_findings=drafts
    )
    _, final, metrics = await FinalizeFindings(model, 1500, 10, 600, 0.55).execute(
        findings=tuple(f for values in merged.values() for f in values),
        experience_by_finding={},
    )
    assert model.calls == 0 and len(final) == 1 and final[0].status == "confirmed"
    assert {location["page"] for location in final[0].evidence_locations} == {13, 14}
    assert all(location["visual_regions"] for location in final[0].evidence_locations)
    assert metrics["candidate_fates"]["counts"] == {"saved": 1}


@pytest.mark.parametrize(
    "change", ["condition", "object", "quote", "page", "compatible"]
)
def test_purpose_proof_requires_bound_identity_conditions_and_values(change):
    """Сам тег или флаг подтверждения не обходят доказательства."""
    from pdrd_analysis_service.domain.evidence_audit import (
        deterministic_purpose_mismatch,
    )

    first = observation("водоснабжение")
    second = observation("водоотведение", 2)
    updates = {
        "condition": {"scope_condition": "проектируемый"},
        "object": {"identifier": "S8"},
        "quote": {"evidence_text": "Система S7 водоснабжение"},
        "page": {"source_pages": (3,)},
        "compatible": {
            "value_raw": "противопожарное водоснабжение",
            "evidence_text": "Система S7 противопожарное водоснабжение",
        },
    }
    second = replace(second, fact=replace(second.fact, **updates[change]))
    assertion = assertion_from_facts((first, second), include_related=True)
    assert not deterministic_purpose_mismatch(
        assertion,
        page_texts={1: first.fact.evidence_text, 2: second.fact.evidence_text},
    )


def test_network_total_lengths_share_identity_but_segments_do_not():
    """Общий механизм сопоставляет сети разных структурированных типов без правил для чисел."""
    items = tuple(
        observation(
            value,
            page,
            kind="parameter",
            subject_type=kind,
            property_type="parameter",
            property_name=prop,
            unit_raw="м",
            evidence_text=f"Общая длина сети S7 {value} м",
            value_role="network_total_length",
        )
        for value, page, kind, prop in (
            ("147", 1, "network", "протяженность сети"),
            ("150", 2, "system", "Длина"),
        )
    )
    groups = discover_conflicts(items, require_physical_identity=True)
    assert len(groups) == 1
    assert (
        audit_assertion(
            assertion_from_facts(items, include_related=True),
            page_texts={item.page: item.fact.evidence_text for item in items},
        ).disposition
        == "supported"
    )
    changed = replace(
        items[1],
        fact=replace(
            items[1].fact,
            evidence_text="S7 участок У1—У2 длина 150 м",
            value_role="segment_length",
            segment_start="У1",
            segment_end="У2",
        ),
    )
    assert discover_conflicts((items[0], changed), require_physical_identity=True) == ()
    typed = replace(
        items[0], fact=replace(items[0].fact, property_type="network_total_length")
    )
    assert (
        len(discover_conflicts((typed, items[1]), require_physical_identity=True)) == 1
    )


@pytest.mark.parametrize(
    "prop,a,b,expected",
    [
        ("Диаметр", "DN100", "Ø110", "different_parameter_definitions"),
        ("Диаметр", "Ø89x3,5", "Ø110", "different_object_kinds"),
        ("Объем резервуара", "100", "50", "individual_object_unidentified"),
    ],
)
def test_counterevidence_precedes_incomplete_quotes(prop, a, b, expected):
    """Разные изделия не становятся вопросом о противоречии при неполной цитате одной стороны."""
    reservoir = "резервуар противопожарный" if "Объем" in prop else "S7"
    items = tuple(
        observation(
            value,
            page,
            kind="parameter",
            identifier=reservoir,
            property_type="parameter",
            property_name=prop,
            unit_raw="м³" if "Объем" in prop else "",
            evidence_text=f"{reservoir} {'сухотруб' if a.startswith('Ø89') and page == 1 else ''} {value}",
            visual_regions=(
                {"x_min": 1, "y_min": 1, "x_max": 5, "y_max": 5, "confidence": 0.9},
            ),
        )
        for value, page in ((a, 1), (b, 2))
    )
    audit = audit_assertion(
        assertion_from_facts(items, include_related=True),
        page_texts={1: "Неполный текст", 2: items[1].fact.evidence_text},
    )
    assert audit.disposition == "diagnostic" and audit.reason_code == expected


async def test_missing_semantic_decision_stays_unassessed_with_complete_facts():
    """Недостаток решения о числовом параметре не превращается в refuted."""
    pages = tuple(
        DocumentPage(
            page,
            f"S7 расход {value} л/с",
            "",
            (
                observation(
                    value,
                    page,
                    kind="parameter",
                    property_type="parameter",
                    property_name="Расход воды",
                    unit_raw="л/с",
                    evidence_text=f"S7 расход {value} л/с",
                ).fact,
            ),
        )
        for page, value in ((15, "0,5205"), (30, "0,205"))
    )
    diagnostics = {}
    assert (
        await CheckCrossPageConsistency(EmptyModel(), _options()).execute(
            pages=pages, diagnostics=diagnostics
        )
        == ()
    )
    record = diagnostics["candidates"][0]
    assert record["disposition"] == "unassessed" and len(record["facts"]) == 2
    assert {item["page"] for item in record["facts"]} == {15, 30}


def test_explicit_individual_reservoir_volume_remains_comparable():
    """Защита от родовых названий не отключает сравнение одной подписанной ёмкости."""
    items = tuple(
        observation(
            value,
            page,
            identifier="Р1",
            kind="parameter",
            property_type="parameter",
            property_name="Объем резервуара",
            unit_raw="м³",
            evidence_text=f"Резервуар Р1 объем {value} м³",
        )
        for page, value in ((1, "100"), (2, "50"))
    )
    assert (
        audit_assertion(
            assertion_from_facts(items, include_related=True),
            page_texts={i.page: i.fact.evidence_text for i in items},
        ).disposition
        == "supported"
    )


@pytest.mark.parametrize(
    "property_a,property_b",
    [
        ("Гарантированный напор", "Требуемый напор"),
        ("Максимальный расход", "Средний расход"),
        ("Расход водопотребления", "Расход водоотведения"),
    ],
)
def test_known_different_parameter_roles_are_never_confirmed(property_a, property_b):
    """Изменение обработки неполной роли не приравнивает явно разные инженерные величины."""
    items = tuple(
        observation(
            value,
            page,
            kind="parameter",
            property_type="parameter",
            property_name="Расход воды",
            column_label=label,
            value_role="flow",
            unit_raw="л/с",
            evidence_text=f"S7 {label} {value} л/с",
        )
        for page, value, label in ((1, "4", property_a), (2, "5", property_b))
    )
    result = audit_assertion(
        assertion_from_facts(items, include_related=True),
        page_texts={i.page: i.fact.evidence_text for i in items},
    )
    assert (
        result.disposition == "diagnostic"
        and result.reason_code == "different_parameter_roles"
    )


def test_day_flow_differences_are_checked_separately_from_volume_and_second_flow():
    """Размерность сохраняет возможность сравнить суточные показатели без смешения с объёмом."""
    items = tuple(
        observation(
            value,
            page,
            kind="parameter",
            property_type="parameter",
            property_name="Расход воды",
            unit_raw=unit,
            evidence_text=f"S7 расход {value} {unit}",
        )
        for page, value, unit in ((1, "100", "м³/сут"), (2, "50", "м3/сут"))
    )
    assert len(discover_conflicts(items)) == 1
    for unit in ("м³", "л/с", "м³/ч"):
        assert (
            discover_conflicts(
                (
                    items[0],
                    replace(items[1], fact=replace(items[1].fact, unit_raw=unit)),
                )
            )
            == ()
        )
