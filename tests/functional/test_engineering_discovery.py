# tests/functional/test_engineering_discovery.py

"""Обнаружение кандидатов из исходных данных PDF до mock-проверки, без готовых findings."""

import json
from pathlib import Path

import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentContextOptions,
    DocumentPage,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.application.vision_execution import (
    VisionExecution,
    vision_execution,
)
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
    document_fact_from_mapping,
)

SOURCE = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures/engineering/stage2-source-excerpts.json"
    ).read_text("utf-8")
)
OPTIONS = DocumentContextOptions(8, 3, 0.5, 20, 8, 5, 1500)


class BlindExtractionModel:
    """Пропускает назначения при Understanding; проверяет только уже обнаруженные противоречия."""

    async def generate_json(self, **request):
        """Mock не создаёт findings и не получает матрицу H01–H35."""
        if request["stage"].startswith("page_understanding:"):
            payload = {
                "discipline": "Водоснабжение",
                "page_type": "text",
                "summary": "Сведения о сетях",
                "objects": [],
                "connections": [],
                "labels": [],
                "normative_queries": [],
                "document_facts": [],
            }
        else:
            candidates = json.loads(request["prompt"].rsplit("\n", 1)[1])
            payload = {
                "decisions": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "status": "confirmed",
                        "reason": "Одному явному идентификатору заданы разные назначения.",
                        "reason_code": "confirmed_evidence",
                    }
                    for candidate in candidates
                ]
            }
        return GenerationResult(
            payload, GenerationMetrics(1, "stop", 1500, 1, 0, 1, 1, 1, 0)
        )


async def document_pages(transform=lambda text: text):
    """Извлекает настоящим сценарием факты текстовых фрагментов контрольного PDF."""
    model = BlindExtractionModel()
    pages = []
    for raw in SOURCE["pages"]:
        text = transform(raw["text"])
        facts, _ = await UnderstandPage(model, 1500).execute(
            page_number=raw["page"],
            heuristic_page_type="text",
            extracted_text=text,
            image_bytes=b"mock-render",
            use_document_context=True,
        )
        pages.append(
            DocumentPage(raw["page"], text, facts.summary, facts.document_facts)
        )
    return tuple(pages)


@pytest.mark.parametrize("tag", ["В2", "S7"])
async def test_system_purpose_is_discovered_without_prebuilt_findings(tag):
    """Назначения из ТЧ создают одну предметную группу даже при пропуске модели; теги не зашиты в runtime."""
    pages = await document_pages(lambda text: text.replace("В2", tag))
    located = tuple(
        LocatedDocumentFact(page.page, index, fact)
        for page in pages
        for index, fact in enumerate(page.facts, 1)
    )
    groups = discover_conflicts(located, max_candidates=20)
    assert len(groups) == 1 and {fact.fact.identifier for fact in groups[0].facts} == {
        tag
    }
    assert {fact.page for fact in groups[0].facts} == {8, 13}
    findings = await CheckCrossPageConsistency(BlindExtractionModel(), OPTIONS).execute(
        pages=pages
    )
    _, final, _ = await FinalizeFindings(
        BlindExtractionModel(), 1500, 10, 600, 0.55
    ).execute(findings=findings, experience_by_finding={})
    assert len(final) == 1 and tag in final[0].comment
    assert {location["page"] for location in final[0].evidence_locations} == {8, 13}
    assert len(final[0].document_context_basis_sources) == 2
    assert final[0].basis_sources == ()


async def test_corrected_system_purpose_has_no_candidate():
    """Исправленный источник не создаёт конфликт только потому, что это контрольный документ."""
    pages = await document_pages(
        lambda text: text.replace("сети водоотведения В2", "сети водоснабжения В2")
    )
    assert (
        await CheckCrossPageConsistency(BlindExtractionModel(), OPTIONS).execute(
            pages=pages
        )
        == ()
    )


def test_real_flow_rows_discover_only_actual_conversion_mismatch():
    """Исходные строки создают кандидат для 0,738/0,5205; правильные пары и периодический режим проходят."""
    located = []
    for row in SOURCE["flow_rows"]:
        for value, unit in ((row["hourly"], "м³/ч"), (row["seconds"], "л/с")):
            fact = document_fact_from_mapping(
                {
                    "kind": "parameter",
                    "subject_type": "system",
                    "identifier": row["row"],
                    "subject_name": row["row"],
                    "property_type": "parameter",
                    "property_name": "Расход воды",
                    "value_raw": value,
                    "unit_raw": unit,
                    "table_id": "Таблица 2",
                    "row_label": row["row"],
                    "scope_operating_mode": row["mode"],
                    "evidence_text": f"{row['row']}: {value} {unit}; {row['mode']}",
                }
            )
            assert fact is not None
            located.append(LocatedDocumentFact(15, len(located) + 1, fact))
    groups = discover_conflicts(tuple(located), max_candidates=20)
    assert len(groups) == 1
    assert groups[0].facts[0].fact.identifier == "Подпитка сетевого контура"
    assert {fact.fact.value_raw for fact in groups[0].facts} == {"0,738", "0,5205"}


async def test_candidate_trace_correlates_job_and_excludes_document_text(caplog):
    """Путь кандидата доступен по ключу задания, а пользовательский текст не попадает в эксплуатационный лог."""

    async def not_cancelled():
        """Эмулирует активное задание без обращения к серверу."""
        return False

    with (
        caplog.at_level("INFO"),
        vision_execution(VisionExecution("stage2-job", not_cancelled)),
    ):
        pages = await document_pages()
        await CheckCrossPageConsistency(BlindExtractionModel(), OPTIONS).execute(
            pages=pages
        )
    events = [
        json.loads(record.message.split("engineering_candidate ", 1)[1])
        for record in caplog.records
        if "engineering_candidate " in record.message
    ]
    assert events and all(event["job"] == "stage2-job" for event in events)
    assert {event["stage"] for event in events} >= {
        "cross_page_hypothesis",
        "cross_page_verification",
    }
    assert "147,00" not in caplog.text and "Протяженность" not in caplog.text


@pytest.mark.parametrize("kind", ["inventory", "sheets"])
async def test_structural_candidates_keep_source_pages_and_specific_recommendation(
    kind,
):
    """Исходные отношения проходят обнаружение и оформление без готовых findings и потери страниц."""
    if kind == "inventory":
        records = [
            (
                1,
                {
                    "kind": "quantity",
                    "subject_type": "device",
                    "identifier": "S7",
                    "property_type": "inventory_count",
                    "value_raw": "1",
                },
            ),
            (
                2,
                {
                    "kind": "relationship",
                    "subject_type": "device",
                    "identifier": "G1",
                    "relation": "member_of",
                    "scope_system": "S7",
                    "value_raw": "S7",
                },
            ),
            (
                2,
                {
                    "kind": "relationship",
                    "subject_type": "device",
                    "identifier": "G2",
                    "relation": "member_of",
                    "scope_system": "S7",
                    "value_raw": "S7",
                },
            ),
        ]
    else:
        records = [
            (
                11,
                {
                    "kind": "identifier",
                    "subject_type": "drawing_document",
                    "identifier": "DOC-A",
                    "property_type": "sheet_number",
                    "value_raw": "2",
                },
            ),
            (
                12,
                {
                    "kind": "identifier",
                    "subject_type": "drawing_document",
                    "identifier": "DOC-A",
                    "property_type": "sheet_number",
                    "value_raw": "4",
                },
            ),
        ]
    by_page = {}
    for page, record in records:
        fact = document_fact_from_mapping(
            {
                "property_name": "Поле документа",
                "evidence_text": f"{record['identifier']}: {record['value_raw']}",
                **record,
            }
        )
        assert fact is not None
        by_page.setdefault(page, []).append(fact)
    pages = tuple(
        DocumentPage(page, "Исходные данные", "", tuple(facts))
        for page, facts in by_page.items()
    )
    findings = await CheckCrossPageConsistency(BlindExtractionModel(), OPTIONS).execute(
        pages=pages
    )
    assert len(findings) == 1
    _, final, _ = await FinalizeFindings(
        BlindExtractionModel(), 1500, 10, 600, 0.55
    ).execute(findings=findings, experience_by_finding={})
    assert len(final) == 1 and len(final[0].document_context_basis_sources) == len(
        records
    )
    assert {source.page for source in final[0].document_context_basis_sources} == set(
        by_page
    )
    if kind == "inventory":
        assert (
            "2 уникальных элементов" in final[0].comment
            and "G1, G2" in final[0].comment
        )
    else:
        assert "последовательности" in final[0].comment
        assert (
            "единому значению" not in final[0].comment
            and "последовательность" in final[0].recommendation
        )
