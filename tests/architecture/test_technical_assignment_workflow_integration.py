# tests/architecture/test_technical_assignment_workflow_integration.py

"""Architecture guards exhaustive T-first integration в n8n workflows."""

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

WORKFLOW_PATHS = (
    ROOT / "n8n" / "workflows" / "analysis-v2-pdf.json",
    ROOT / "n8n" / "workflows" / "analysis-v2-cad.json",
    ROOT / "n8n" / "workflows" / "analysis-v2-pdf-cad.json",
)

REQUIRED_T_FIRST_NODES = {
    "Has Technical Assignment",
    "Load Technical Assignment Requirements",
    "Technical Assignment Requirement Feed",
    "Has T First Pass",
    "Check Technical Assignment",
    "Technical Assignment First Pass",
    "Merge Finding Candidates",
}


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


def _serialized_node(
    nodes: dict[str, dict[str, Any]],
    name: str,
) -> str:
    """Сериализует node для architecture assertions."""
    return json.dumps(
        nodes[name],
        ensure_ascii=False,
    )


def test_every_workflow_loads_complete_atomic_t_feed() -> None:
    """Все workflow получают bounded paginated atomic T-R feed."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        assert (
            set(
                nodes,
            )
            >= REQUIRED_T_FIRST_NODES
        ), path

        load = nodes["Load Technical Assignment Requirements"]

        assert load["type"] == "n8n-nodes-base.httpRequest", path

        serialized = _serialized_node(
            nodes,
            "Load Technical Assignment Requirements",
        )

        assert "/requirements" in serialized, path

        assert '"limit", "value": "200"' in serialized, path

        assert "updateAParameterInEachRequest" in serialized, path

        assert "$pageCount * 200" in serialized, path

        assert '"maxRequests": 5' in serialized, path

        feed = _serialized_node(
            nodes,
            "Technical Assignment Requirement Feed",
        )

        assert "requirements.length !== total" in feed, path

        assert "new Set(requirementIds).size" in feed, path

        assert "total > 1000" in feed, path

        assert "analysis_document_id" in feed, path

        assert "section_id" in feed, path

        assert "source_sha256" in feed, path


def test_every_workflow_runs_independent_t_first_check() -> None:
    """Atomic feed идёт в отдельный exhaustive Analysis endpoint."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        check = _serialized_node(
            nodes,
            "Check Technical Assignment",
        )

        assert "/internal/v1/pages/check-technical-assignment" in check, path

        assert "requirements" in check, path

        assert "page_facts" in check, path

        assert "image_base64" in check, path

        normalized = _serialized_node(
            nodes,
            "Technical Assignment First Pass",
        )

        assert "decisions.length !== requirementsCount" in normalized, path

        assert "expectedIds.some" in normalized, path

        assert "new Set(decisionIds).size" in normalized, path

        assert "findings" in normalized, path


def test_t_first_findings_are_merged_without_semantic_filtering() -> None:
    """N-check и T-first findings объединяются lossless перед enrichment."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        merge = _serialized_node(
            nodes,
            "Merge Finding Candidates",
        )

        assert "normativeFindings" in merge, path

        assert "technicalAssignmentFindings" in merge, path

        assert "...normativeFindings" in merge, path

        assert "...technicalAssignmentFindings" in merge, path

        assert "confidence" not in merge, path

        assert ".filter(" not in merge, path

        prepare = _serialized_node(
            nodes,
            "Prepare Finding Normative Queries",
        )

        assert ".slice(0, 10)" not in prepare, path


def test_t_first_is_optional_without_changing_non_t_analysis_path() -> None:
    """Отсутствие ТЗ проходит через explicit bypass, а не fake T payload."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        feed = _serialized_node(
            nodes,
            "Technical Assignment Requirement Feed",
        )

        assert "enabled: false" in feed, path

        assert "requirements: []" in feed, path

        first_pass = _serialized_node(
            nodes,
            "Technical Assignment First Pass",
        )

        assert "enabled: false" in first_pass, path

        assert "requirements_count: 0" in first_pass, path

        assert "findings: []" in first_pass, path


def test_workflows_expose_t_first_diagnostics_and_pipeline_stage() -> None:
    """Runtime result показывает полноту T-first обработки."""
    for path in WORKFLOW_PATHS:
        serialized = json.dumps(
            _workflow(
                path,
            ),
            ensure_ascii=False,
        )

        assert "technical_assignment_requirement_feed" in serialized, path

        assert "technical_assignment_first_pass" in serialized, path

        assert "finding_candidate_merge" in serialized, path

        assert "requirements_count" in serialized, path

        assert "decisions_count" in serialized, path

        assert "violated_count" in serialized, path

        assert "needs_review_count" in serialized, path
