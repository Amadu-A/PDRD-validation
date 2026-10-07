# tests/architecture/test_document_context_flow.py

"""Архитектурные границы контекста проверяемого PDF."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _workflow() -> dict[str, object]:
    """Читает опубликованный в репозитории PDF workflow."""
    path = ROOT / "n8n/workflows/analysis-v2-pdf.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _next(workflow: dict[str, object], node: str) -> list[str]:
    """Возвращает имена следующих узлов первой ветви."""
    return [item["node"] for item in workflow["connections"][node]["main"][0]]


def test_document_context_is_built_once_and_used_before_page_check() -> None:
    """Индекс всего PDF создаётся после понимания страниц и до проверки N/T/U."""
    workflow = _workflow()
    names = {node["name"] for node in workflow["nodes"]}
    assert len(names) == len(workflow["nodes"])
    assert _next(workflow, "Understand Pages Stage") == [
        "Progress Build Document Context"
    ]
    assert _next(workflow, "Create Document Context") == ["Understand Page"]
    assert _next(workflow, "Search Document Context") == [
        "Gate Collect Page Document Context"
    ]
    assert _next(workflow, "Build Page Document Context") == [
        "Expand Page Document Context"
    ]
    assert _next(workflow, "Expand Page Document Context") == [
        "Build Normative Queries"
    ]
    check = next(node for node in workflow["nodes"] if node["name"] == "Check Norms")
    assert "document_context_sources" in check["parameters"]["body"]


def test_cross_page_stage_is_once_per_document_and_before_finalization() -> None:
    """Отдельная проверка получает все страницы и объединяет замечания до обогащения."""
    workflow = _workflow()
    check = next(
        node
        for node in workflow["nodes"]
        if node["name"] == "Check Cross-Page Consistency"
    )
    assert "$('Understand Page').all()" in check["parameters"]["body"]
    assert _next(workflow, "Check Norms") == ["Progress Cross-Page Consistency"]
    assert _next(workflow, "Check Cross-Page Consistency") == [
        "Merge Cross-Page Checks"
    ]
    assert _next(workflow, "Merge Cross-Page Checks") == [
        "Gate Expand Norm Check Stage"
    ]
    assert _next(workflow, "Gate Expand Norm Check Stage") == [
        "Merge Finding Candidates"
    ]


def test_document_switch_controls_all_d_stages_and_single_merge():
    """Workflow передаёт выбор пользователя в каждый затратный D-этап."""
    nodes = {node["name"]: node for node in _workflow()["nodes"]}
    for name in (
        "Create Document Context",
        "Search Document Context",
        "Build Page Document Context",
        "Check Cross-Page Consistency",
    ):
        assert "use_document_context" in nodes[name]["parameters"]["body"]
        assert "enabled:" in nodes[name]["parameters"]["body"]
    assert (
        "use_document_context"
        in nodes["Gate Collect Understanding Stage"]["parameters"]["jsCode"]
    )
    assert "page_checks:" in nodes["Check Cross-Page Consistency"]["parameters"]["body"]
    assert (
        "Array.isArray($json.items)"
        in nodes["Merge Cross-Page Checks"]["parameters"]["jsCode"]
    )
