# tests/architecture/test_workflow_payload.py

"""Защищает Runner многостраничного PDF от растров и запроса всей истории."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "n8n" / "workflows" / "analysis-v2-pdf.json"


def test_pdf_metadata_is_compacted_before_any_runner_uses_it() -> None:
    """Проекция выполняется основным процессом после сохранения визуализации."""
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    compact = nodes["Compact PDF Metadata"]
    assert compact["type"] == "n8n-nodes-base.set"
    assert compact["parameters"]["includeOtherFields"] is False
    for source, target in (
        ("Persist Visualization Artifact", "Compact PDF Metadata"),
        ("Compact PDF Metadata", "Resolve Project Context Cache"),
    ):
        assert workflow["connections"][source]["main"][0][0]["node"] == target
    assert "Compact PDF Metadata" in nodes["Expand PDF Pages"]["parameters"]["jsCode"]


def test_pdf_runner_never_requests_rasters_or_all_execution_nodes() -> None:
    """В n8n .item и динамический селектор передают Runner весь runData."""
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    for node in workflow["nodes"]:
        if node["type"] != "n8n-nodes-base.code":
            continue
        code = node["parameters"]["jsCode"]
        for forbidden in ("Document Extract PDF", "image_base64", "text_words"):
            assert forbidden not in code, (node["name"], forbidden)
        assert not re.search(r"\.(?:item|pairedItem|itemMatching)\b", code), node[
            "name"
        ]
        assert not re.search(r"\$(?:node|item)\b", code), node["name"]
        assert not re.search(r"\$\(\s*(?!['\"])", code), node["name"]


def test_pdf_page_metadata_does_not_repeat_full_assignment_feed() -> None:
    """Полные требования нужны сборщику T-first, а не каждой копии страницы."""
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    expand = nodes["Expand PDF Pages"]["parameters"]["jsCode"]
    assert "technical_assignment: technicalAssignment" not in expand
    for name in (
        "Gate Collect Technical Assignment Stage",
        "Technical Assignment First Pass",
    ):
        assert (
            "Technical Assignment Requirement Feed"
            in nodes[name]["parameters"]["jsCode"]
        )
