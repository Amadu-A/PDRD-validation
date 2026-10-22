# tests/functional/test_quality_stage2_pipeline.py

"""Исходные слова PDF, продолжение таблицы и эквивалентность транспортного контекста."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from pdrd_analysis_service.application.engineering_program import (
    audit_engineering_candidates,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentContextOptions,
    DocumentPage,
    document_engineering_report,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import FindingDraft
from pdrd_analysis_service.domain.calculation_tables import calculation_tables_by_page
from pdrd_analysis_service.domain.document_context import LocatedDocumentFact
from pdrd_analysis_service.domain.engineering_facts import counted_storage_components
from pdrd_analysis_service.domain.evidence_audit import bind_evidence
from pdrd_analysis_service.transport.http.stage_batch_schemas import (
    CheckNormsStageRequest,
)


def source():
    """Возвращает только исходные слова и физические страницы, без ожидаемых замечаний."""
    return json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/engineering/ios2-calculation-continuation.json"
        ).read_text("utf-8")
    )["pages"]


def test_real_headless_continuation_retains_rows_headers_and_physical_geometry():
    """Строка подпитки существует в продолжении таблицы 1, отдельно от новой таблицы 2."""
    pages = source()
    tables = calculation_tables_by_page(
        tuple((p["page"], p["text_words"]) for p in pages)
    )
    table1 = tables[14][0].table_id
    continued = [
        f
        for f in tables[15]
        if f.table_id == table1 and f.row_label == "Подпитка сетевого контура"
    ]
    assert {f.value_raw for f in continued} == {"17,70", "0,738", "0,5205"}
    texts = {p["page"]: p["text"] for p in pages}
    for index, fact in enumerate(continued, 1):
        assert fact.source_pages == (14, 15)
        assert {r["page"] for r in fact.source_regions} == {14, 15}
        assert (
            bind_evidence(LocatedDocumentFact(15, index, fact), texts).status
            == "supported"
        )
    assert any(
        f.table_id != table1 and f.row_label == "Подпитка сетевого контура"
        for f in tables[15]
    )
    assert all(
        "Инв" not in f.row_label and "Взам" not in f.row_label for f in tables[15]
    )
    assert (
        calculation_tables_by_page(
            tuple((p["page"], p["text_words"]) for p in pages), link_continuations=False
        )[15][0].table_id
        != table1
    )


def test_missing_row_claim_is_refuted_by_real_continuation_not_by_new_table():
    """Отсутствие строки на первом физическом листе не доказывает её отсутствия в таблице."""
    pages = tuple(
        DocumentPage(p["page"], p["text"], "", (), tuple(p["text_words"]))
        for p in source()
    )
    report = document_engineering_report(pages, "continuation")
    candidate = FindingDraft(
        "missing-row",
        14,
        "table",
        "document_consistency",
        "warning",
        "confirmed",
        "В таблице 1 отсутствует строка «Подпитка сетевого контура».",
        "Таблица 1",
        "Уточнить состав строк",
        0.9,
        (),
        " ",
        (),
        "",
    )
    (checked,) = audit_engineering_candidates((candidate,), report)
    assert checked.status == "hypothesis"
    audit = checked.origin_assertions[-1]
    assert audit["reason_code"] == "claimed_missing_row_present_in_continuation"
    assert {o["page"] for o in audit["observations"]} == {15}
    assert all(o["fact"]["source_fragments"] for o in audit["observations"])


def test_counted_reservoir_classes_are_composition_not_incompatible_volumes():
    """Два по 100 и один по 50 сохраняются как разные компоненты без вывода о полезном запасе."""
    text = "Резервный источник: два резервуара объемом 100 м3 и один резервуар объемом 50 м3."
    components = counted_storage_components(text)
    assert [(c["count"], c["volume_raw"]) for c in components] == [
        (2, "100"),
        (1, "50"),
    ]
    assert all(c["evidence_text"] in text for c in components)
    listed = counted_storage_components(
        "Установка трех резервуаров противопожарного запаса воды, объемом 100 м3, 100 м3, 50 м3."
    )
    assert [(c["count"], c["volume_raw"]) for c in listed] == [(2, "100"), (1, "50")]


async def test_real_h16_flow_comparison_keeps_native_conditions_and_reaches_final_question():
    """Расхождение двух секундных расходов доходит до результата с границами доказанности."""
    raw = source()
    raw.append(
        json.loads(
            (
                Path(__file__).parents[1]
                / "fixtures/engineering/ios2-calculation-page30.json"
            ).read_text("utf-8")
        )
        | {"page": 30}
    )
    pages = tuple(
        DocumentPage(p["page"], p["text"], "", (), tuple(p["text_words"])) for p in raw
    )

    class Model:
        """Имитирует только решение о заданных кандидатах, замечаний не создаёт."""

        async def generate_json(self, **request):
            """Одинаково подтверждает сопоставимость, которую затем повторно проверяет код."""
            keys = request["schema"]["properties"]["decisions"]["items"]["properties"][
                "candidate_id"
            ]["enum"]
            return SimpleNamespace(
                payload={
                    "decisions": [
                        {
                            "candidate_id": key,
                            "status": "confirmed",
                            "reason": "Сопоставлены явно подписанные показатели одной строки и режима.",
                        }
                        for key in keys
                    ]
                }
            )

    diagnostics = {}
    drafts = await CheckCrossPageConsistency(
        Model(), DocumentContextOptions(10, 4, 0.45, 24, 5, 8, 1000)
    ).execute(pages=pages, diagnostics=diagnostics)
    flow = [f for f in drafts if "0,5205" in f.comment and "0,205" in f.comment]
    assert len(flow) == 1 and flow[0].status == "needs_review"
    _, final, _ = await FinalizeFindings(Model(), 1500, 10, 600, 0.55).execute(
        findings=tuple(flow), experience_by_finding={}
    )
    assert len(final) == 1
    assert {location["page"] for location in final[0].evidence_locations} == {15, 30}
    assert all(s.visual_regions for s in final[0].document_context_basis_sources)


def test_packed_and_native_context_produce_identical_programme_with_page_bound_sources():
    """Одинаковая цитата на разных страницах сохраняет собственные ID, условия и области."""
    quote = "S7 участок У1—У2, расход 4 л/с, штатный режим."
    region = {"x_min": 10, "y_min": 20, "x_max": 60, "y_max": 80, "confidence": 0.9}
    original = {"document_id": "00000000-0000-4000-8000-000000000001", "items": []}
    for n in range(1, 32):
        original["items"].append(
            {
                "page_number": n,
                "extracted_text": quote,
                "image_base64": "eA==",
                "page_facts": {
                    "discipline": "",
                    "page_type": "drawing",
                    "summary": "",
                    "objects": [],
                    "connections": [],
                    "labels": [],
                    "normative_queries": [],
                    "document_facts": [
                        {
                            "kind": "parameter",
                            "subject_type": "system",
                            "identifier": "S7",
                            "property_type": "parameter",
                            "property_name": "Расход",
                            "value_raw": "4",
                            "unit_raw": "л/с",
                            "scope_segment": "У1—У2",
                            "scope_operating_mode": "штатный режим",
                            "evidence_text": quote,
                            "visual_regions": [region],
                            "source_pages": [n],
                        }
                    ],
                },
                "normative_sources": [
                    {
                        "source_id": f"N{n}",
                        "point_id": f"00000000-0000-4000-8000-{n:012d}",
                        "page": n,
                        "score": 1,
                        "text": quote,
                    }
                ],
                "technical_assignment_sources": [
                    {"source_id": f"T{n}", "page": n, "score": 1, "text": quote}
                ],
                "user_package_sources": [
                    {"source_id": f"U{n}", "page": n, "score": 1, "text": quote}
                ],
                "document_context_sources": [
                    {
                        "source_id": f"D-p{n:04d}-c0",
                        "page": n,
                        "chunk_index": 0,
                        "text": quote,
                        "evidence_text": quote,
                        "visual_regions": [region],
                    }
                ],
                "project_context_sources": [
                    {
                        "source_id": f"PZ{n}",
                        "page": 32 - n,
                        "text": "ПЗ: другой объект S8, расход 8 л/с.",
                    }
                ],
            }
        )
    packed = deepcopy(original)
    packed.update(
        context_encoding="text-pool-v1", context_pool=[quote], context_refs=[]
    )
    for n, item in enumerate(packed["items"]):
        for field in ("extracted_text",):
            item[field] = None
            packed["context_refs"].append({"path": ["items", n, field], "index": 0})
        for field in (
            "normative_sources",
            "technical_assignment_sources",
            "user_package_sources",
            "document_context_sources",
        ):
            item[field][0]["text"] = None
            packed["context_refs"].append(
                {"path": ["items", n, field, 0, "text"], "index": 0}
            )
    requests = [
        CheckNormsStageRequest.model_validate(data) for data in (original, packed)
    ]
    assert requests[0].model_dump() == requests[1].model_dump()
    programmes = [
        document_engineering_report(
            tuple(
                DocumentPage(
                    i.page_number,
                    i.extracted_text,
                    "",
                    i.page_facts.to_domain().document_facts,
                )
                for i in req.items
            ),
            "transport",
        ).as_dict()
        for req in requests
    ]
    assert programmes[0] == programmes[1]
    assert len(programmes[0]["evidence"]) == 31
