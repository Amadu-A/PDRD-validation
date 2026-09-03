# tests/architecture/test_normative_snapshot_workflows.py

"""Architecture tests передачи normative snapshot через n8n workflows."""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

WORKFLOW_CASES = (
    (
        "analysis-v2-pdf.json",
        "POST /analysis/v2/pdf",
    ),
    (
        "analysis-v2-cad.json",
        "POST /analysis/v2/cad",
    ),
    (
        "analysis-v2-pdf-cad.json",
        "POST /analysis/v2/pdf-cad",
    ),
)


@pytest.mark.parametrize(
    (
        "file_name",
        "webhook_name",
    ),
    WORKFLOW_CASES,
)
def test_normative_snapshot_reaches_search_and_check_nodes(
    file_name: str,
    webhook_name: str,
) -> None:
    """Каждый V2 workflow использует scope и snapshotted prompt."""
    path = PROJECT_ROOT / "n8n" / "workflows" / file_name

    payload = json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )

    nodes = {node["name"]: node for node in payload["nodes"]}

    assert webhook_name in nodes

    search_body = nodes["Search Normative"]["parameters"]["body"]

    check_body = nodes["Check Norms"]["parameters"]["body"]

    assert "normative_section_id" in search_body

    assert "normative_document_ids" in search_body

    assert "normative_system_prompt" in check_body
