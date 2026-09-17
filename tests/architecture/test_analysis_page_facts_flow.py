# tests/architecture/test_analysis_page_facts_flow.py

"""Architecture guards provenance PageFacts после T-first веток."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

WORKFLOW_ROOT = ROOT / "n8n" / "workflows"

WORKFLOW_CASES = (
    (
        WORKFLOW_ROOT / "analysis-v2-pdf.json",
        {
            "Build Project Context Query": ("$('Understand Page').item.json.facts"),
            "Build Normative Queries": ("$('Understand Page').item.json.facts"),
        },
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-cad.json",
        {
            "Build Normative Queries": ("$('Understand CAD').item.json.facts"),
        },
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        {
            "Build Project Context Query": (
                "$('Understand Combined Page').item.json.facts"
            ),
            "Build Normative Queries": (
                "$('Understand Combined Page').item.json.facts"
            ),
        },
    ),
)

IMAGE_FLOW_CASES = (
    (
        WORKFLOW_ROOT / "analysis-v2-pdf.json",
        "Understand Page",
        "Check Norms",
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-cad.json",
        "Understand CAD",
        "Check Norms",
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        "Understand Combined Page",
        "Check Norms",
    ),
)


def _workflow(
    path: Path,
) -> dict[str, Any]:
    """Читает committed n8n workflow."""
    payload = json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )

    assert isinstance(
        payload,
        dict,
    )

    return payload


def _nodes_by_name(
    workflow: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Индексирует workflow nodes по имени."""
    nodes = workflow.get(
        "nodes",
        [],
    )

    assert isinstance(
        nodes,
        list,
    )

    return {
        str(
            node["name"],
        ): node
        for node in nodes
        if isinstance(
            node,
            dict,
        )
        and "name" in node
    }


def _request_body(
    node: dict[str, Any],
) -> str:
    """Возвращает expression HTTP request body."""
    parameters = node.get(
        "parameters",
        {},
    )

    assert isinstance(
        parameters,
        dict,
    )

    body = parameters.get(
        "body",
        "",
    )

    assert isinstance(
        body,
        str,
    )

    return body


def test_downstream_queries_use_explicit_page_understanding_facts() -> None:
    """T-first output не должен затереть PageFacts для следующих стадий."""
    for path, expected_nodes in WORKFLOW_CASES:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        for node_name, expected_reference in expected_nodes.items():
            body = _request_body(
                nodes[node_name],
            )

            assert expected_reference in body, (
                path,
                node_name,
            )

            assert "page_facts: $json.facts" not in body, (
                path,
                node_name,
            )


def test_analysis_workflows_keep_image_for_vlm_stages() -> None:
    """PDF/CAD analysis сохраняет изображение для multimodal проверки."""
    for path, understand_name, check_name in IMAGE_FLOW_CASES:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        understand_body = _request_body(
            nodes[understand_name],
        )

        check_body = _request_body(
            nodes[check_name],
        )

        assert "image_base64" in understand_body, path

        assert "image_base64" in check_body, path
