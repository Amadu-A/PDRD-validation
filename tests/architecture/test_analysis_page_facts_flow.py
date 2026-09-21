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
            "Build Project Context Query": ("page_facts: $json.facts"),
            "Build Normative Queries": (
                "$('Technical Assignment First Pass').all()[$itemIndex].json.facts"
            ),
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
        if (
            isinstance(
                node,
                dict,
            )
            and "name" in node
        )
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


def _node_code(
    node: dict[str, Any],
) -> str:
    """Возвращает jsCode Code node."""
    parameters = node.get(
        "parameters",
        {},
    )

    assert isinstance(
        parameters,
        dict,
    )

    code = parameters.get(
        "jsCode",
        "",
    )

    assert isinstance(
        code,
        str,
    )

    return code


def test_downstream_queries_use_explicit_page_understanding_facts() -> None:
    """Batch/T-first output не должен затереть PageFacts."""
    for (
        path,
        expected_nodes,
    ) in WORKFLOW_CASES:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        for (
            node_name,
            expected_reference,
        ) in expected_nodes.items():
            body = _request_body(
                nodes[node_name],
            )

            assert expected_reference in body, (
                path,
                node_name,
            )


def test_pdf_stage_batch_keeps_image_for_understanding() -> None:
    """PDF batch collector не теряет image перед understanding stage."""
    workflow = _workflow(
        WORKFLOW_ROOT / "analysis-v2-pdf.json",
    )

    nodes = _nodes_by_name(
        workflow,
    )

    collect_code = _node_code(
        nodes["Gate Collect Understanding Stage"],
    )

    stage_body = _request_body(
        nodes["Understand Pages Stage"],
    )

    assert "image_base64" in collect_code

    assert "item.page.image_base64" in collect_code

    assert "items: $json.items" in stage_body


def test_pdf_stage_batch_keeps_image_for_normative_check() -> None:
    """PDF norm-check collector сохраняет multimodal image каждой страницы."""
    workflow = _workflow(
        WORKFLOW_ROOT / "analysis-v2-pdf.json",
    )

    nodes = _nodes_by_name(
        workflow,
    )

    collect_code = _node_code(
        nodes["Gate Collect Norm Check Stage"],
    )

    stage_body = _request_body(
        nodes["Check Norms"],
    )

    assert "image_base64" in collect_code

    assert "page.expanded.page.image_base64" in collect_code

    assert "image_base64" in stage_body


def test_cad_and_combined_workflows_keep_direct_vlm_images() -> None:
    """Однолистовые CAD modes сохраняют direct multimodal contract."""
    cases = (
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

    for (
        path,
        understand_name,
        check_name,
    ) in cases:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        assert "image_base64" in _request_body(
            nodes[understand_name],
        ), path

        assert "image_base64" in _request_body(
            nodes[check_name],
        ), path
