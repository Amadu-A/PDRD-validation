# tests/architecture/test_large_document_resilience.py

"""Architecture guards large-document resilience with shared vLLM."""

import json
from pathlib import Path
from typing import Any

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

WORKFLOW_ROOT = ROOT / "n8n" / "workflows"

PDF_WORKFLOW = WORKFLOW_ROOT / "analysis-v2-pdf.json"

ALL_WORKFLOWS = (
    PDF_WORKFLOW,
    (WORKFLOW_ROOT / "analysis-v2-cad.json"),
    (WORKFLOW_ROOT / "analysis-v2-pdf-cad.json"),
)

STAGE_BATCH_ROUTES = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "transport"
    / "http"
    / "stage_batch_routes.py"
)


def _workflow(
    path: Path,
) -> dict[str, Any]:
    """Читает workflow JSON."""
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


def _nodes(
    workflow: dict[str, Any],
) -> dict[
    str,
    dict[
        str,
        Any,
    ],
]:
    """Индексирует nodes."""
    raw_nodes = workflow["nodes"]

    assert isinstance(
        raw_nodes,
        list,
    )

    return {
        str(
            node["name"],
        ): node
        for node in raw_nodes
        if (
            isinstance(
                node,
                dict,
            )
            and "name" in node
        )
    }


def _successors(
    workflow: dict[str, Any],
    source: str,
) -> list[str]:
    """Возвращает первый main output."""
    return [
        str(
            item["node"],
        )
        for item in (workflow["connections"][source]["main"][0])
    ]


def test_all_analysis_workflows_allow_nearly_one_hour() -> None:
    """n8n timeout меньше Gateway/Application/Celery deadlines."""
    for path in ALL_WORKFLOWS:
        workflow = _workflow(
            path,
        )

        assert workflow["settings"]["executionTimeout"] == 3450, path


def test_pdf_vlm_stages_are_document_scoped() -> None:
    """PDF-only сохраняет один HTTP stage на document phase."""
    workflow = _workflow(
        PDF_WORKFLOW,
    )

    nodes = _nodes(
        workflow,
    )

    assert nodes["Understand Pages Stage"]["parameters"]["url"] == (
        "http://pdrd-analysis-service:8501/internal/v1/stages/understand-pages"
    )

    assert nodes["Check Technical Assignment"]["parameters"]["url"] == (
        "http://pdrd-analysis-service:8501/"
        "internal/v1/stages/"
        "check-technical-assignment"
    )

    assert nodes["Check Norms"]["parameters"]["url"] == (
        "http://pdrd-analysis-service:8501/internal/v1/stages/check-norms"
    )

    assert nodes["Finalize Findings"]["parameters"]["url"] == (
        "http://pdrd-analysis-service:8501/internal/v1/stages/finalize"
    )

    for node_name in (
        "Understand Pages Stage",
        "Check Technical Assignment",
        "Check Norms",
        "Finalize Findings",
    ):
        assert nodes[node_name]["parameters"]["options"]["timeout"] == 3_300_000


def test_pdf_stage_batch_topology_is_ordered() -> None:
    """Stage собирается и expand-ится в прежнем deterministic порядке."""
    workflow = _workflow(
        PDF_WORKFLOW,
    )

    assert _successors(
        workflow,
        "Gate Understand Sheet",
    ) == [
        "Gate Collect Understanding Stage",
    ]

    assert _successors(
        workflow,
        "Gate Collect Understanding Stage",
    ) == [
        "Understand Pages Stage",
    ]

    assert _successors(
        workflow,
        "Understand Pages Stage",
    ) == [
        "Understand Page",
    ]

    assert _successors(
        workflow,
        "Understand Page",
    ) == [
        "Has T First Pass",
    ]

    assert _successors(
        workflow,
        "Gate Check Requirements",
    ) == [
        "Gate Collect Norm Check Stage",
    ]

    assert _successors(
        workflow,
        "Gate Collect Norm Check Stage",
    ) == [
        "Check Norms",
    ]

    assert _successors(
        workflow,
        "Check Norms",
    ) == [
        "Gate Expand Norm Check Stage",
    ]

    assert _successors(
        workflow,
        "Gate Expand Norm Check Stage",
    ) == [
        "Merge Finding Candidates",
    ]

    assert _successors(
        workflow,
        "Gate Finalize Findings",
    ) == [
        "Gate Collect Finalization Stage",
    ]

    assert _successors(
        workflow,
        "Gate Collect Finalization Stage",
    ) == [
        "Finalize Findings",
    ]

    assert _successors(
        workflow,
        "Finalize Findings",
    ) == [
        "Progress Build Result",
        "Gate Build Result",
    ]

    assert _successors(
        workflow,
        "Gate Build Result",
    ) == [
        "Gate Expand Finalization Stage",
    ]


def test_document_scoped_stages_use_bounded_vllm_concurrency() -> None:
    """Stage route использует shared-vlm concurrency вместо GPU residency."""
    source = STAGE_BATCH_ROUTES.read_text(
        encoding="utf-8",
    )

    for path in (
        "/internal/v1/stages/understand-pages",
        ("/internal/v1/stages/check-technical-assignment"),
        "/internal/v1/stages/check-norms",
        "/internal/v1/stages/finalize",
    ):
        assert path in source

    assert "_run_stage_items(" in source

    assert "vlm_stage_concurrency" in source

    assert "asyncio.gather(" in source

    assert "residency_scope" not in source

    assert "gpu_stage_" not in source


def test_one_hour_runtime_hierarchy_is_committed() -> None:
    """Canonical env сохраняет deadlines без project VLM lease."""
    source = (ROOT / ".env.example").read_text(
        encoding="utf-8",
    )

    assert "API_GATEWAY_ORCHESTRATION__REQUEST_TIMEOUT_SECONDS=3480" in source

    assert "API_GATEWAY_LIFECYCLE__MAX_RUNTIME_SECONDS=3540" in source

    assert "API_GATEWAY_LIFECYCLE__TASK_SOFT_TIME_LIMIT_SECONDS=3570" in source

    assert "API_GATEWAY_LIFECYCLE__TASK_HARD_TIME_LIMIT_SECONDS=3600" in source

    assert "ANALYSIS_SERVICE_VLM__REQUEST_TIMEOUT_SECONDS=600" in source

    assert "ANALYSIS_SERVICE_PIPELINE__MAX_STAGE_PAGES=50" in source

    assert "ANALYSIS_SERVICE_PIPELINE__VLM_STAGE_CONCURRENCY=4" in source

    assert "ANALYSIS_SERVICE_GPU__" not in source
