# tests/functional/test_quality_resilience.py

"""Пилот H02/H08/H12 на исходных проектных фрагментах без готового списка замечаний."""

import json
from pathlib import Path

from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
)
from pdrd_analysis_service.domain.engineering_facts import merge_text_engineering_facts

from tests.functional.test_engineering_evidence_gate import finalize
from tests.functional.test_quality_completeness import source_pages


async def test_raw_project_choices_add_reserve_and_protected_object_questions_preserving_old_results():
    """Новая программа добавляет предметные вопросы, сохраняя расход, гидравлику и прежнее противоречие ссылок."""
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/engineering/stage6-source-excerpts.json"
        ).read_text("utf-8")
    )
    extra = tuple(
        DocumentPage(
            page["page"],
            page["text"],
            "",
            merge_text_engineering_facts((), page["text"], page["page"]),
        )
        for page in fixture["pages"]
    )
    report = document_engineering_report((*source_pages(), *extra), "native-pilot")
    drafts = program_findings(report)
    _, final, metrics = await finalize(*drafts)
    checks = [
        origin["check"]
        for finding in final
        for origin in finding.origin_assertions
        if origin.get("origin") == "programmatic_engineering_check"
    ]
    dependencies = {
        check["property_key"]
        for check in checks
        if check["rule"] == "design_basis_dependency"
    }
    assert {
        "fire_storage_basis",
        "fire_flow_basis",
        "protected_object_basis",
        "pipe_selection_basis",
    } <= dependencies
    reserve = next(
        finding
        for finding in final
        if any(
            origin.get("check", {}).get("property_key") == "fire_storage_basis"
            for origin in finding.origin_assertions
        )
    )
    assert reserve.page == 18 and reserve.status == "needs_review"
    assert (
        "100 м3, 100 м3, 50 м3" in reserve.comment
        and "полезный объём" in reserve.comment
    )
    assert any(
        "склада угля" in finding.comment and finding.page == 8 for finding in final
    )
    assert any(check["rule"] == "topic_reference_consistency" for check in checks)
    assert all(verified_programmatic_check(finding) for finding in drafts)
    assert len(final) == len(drafts)
    assert {finding.finding_id: finding.comment for finding in final} == {
        finding.finding_id: finding.comment for finding in drafts
    }
    assert not any(finding.basis_sources for finding in final)
    assert metrics["vlm_call_count"] == 0
