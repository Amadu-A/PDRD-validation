# tests/architecture/test_equipment_workflow_flags.py

"""Проверяет условную передачу EQ-флага в PDF Understanding workflow."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pdf_workflow_passes_equipment_flag_into_existing_understanding() -> None:
    """EQ не создаёт второй page VLM-pass и управляется флагом заявки."""
    workflow = json.loads(
        (ROOT / "n8n/workflows/analysis-v2-pdf.json").read_text(encoding="utf-8")
    )
    nodes = {node["name"]: node for node in workflow["nodes"]}
    assert len(nodes) == len(workflow["nodes"])
    assert (
        "use_equipment_web_search"
        in (nodes["Gate Collect Understanding Stage"]["parameters"]["jsCode"])
    )
    body = nodes["Understand Pages Stage"]["parameters"]["body"]
    assert "use_equipment_web_search: item.use_equipment_web_search" in body
    assert [name for name in nodes if name == "Understand Pages Stage"] == [
        "Understand Pages Stage"
    ]


def test_equipment_candidates_merge_before_normative_enrichment() -> None:
    """EQ-ветвь стартует после Understanding и входит в общий поиск нормативов."""
    workflow = json.loads(
        (ROOT / "n8n/workflows/analysis-v2-pdf.json").read_text(encoding="utf-8")
    )
    connections = workflow["connections"]
    nodes = {node["name"] for node in workflow["nodes"]}

    def targets(name: str, output: int = 0) -> set[str]:
        return {edge["node"] for edge in connections[name]["main"][output]}

    assert len(nodes) == len(workflow["nodes"])
    assert "Use Equipment Search" in targets("Understand Pages Stage")
    assert targets("Use Equipment Search", 1) == {"Use Document Context"}
    assert targets("Use Equipment Results", 1) == {"Restore Equipment Pages"}
    assert "Collect Equipment Candidates" in targets("Merge Finding Candidates")
    assert targets("Merge Equipment Candidates") == {"Gate Enrich Findings"}
    assert targets("Gate Enrich Findings") == {"Prepare Finding Normative Queries"}
    assert targets("Gate Collect Finalization Stage") == {"Finalize Findings"}


def test_runtime_stages_share_common_vision_admission(monkeypatch) -> None:
    """EQ и обычные use cases используют один model delegate и общий limiter."""
    from pdrd_analysis_service.application.limited_vision import LimitedVisionModel
    from pdrd_analysis_service.core import container as wiring
    from pdrd_analysis_service.core.settings import Settings

    monkeypatch.setattr(wiring, "get_settings", lambda: Settings(_env_file=None))
    container = wiring.build_container()
    model = container.understand_page.vision_model
    assert isinstance(model._delegate, LimitedVisionModel)
    assert model is container.extract_equipment_vision.vision_model
    assert model is container.check_page_against_norms.vision_model
    assert model is container.check_page_against_technical_assignment.vision_model
    assert model is container.finalize_findings.vision_model
