# tests/architecture/test_n8n_requirement_workflows.py

"""Architecture guards parity N/T/U для всех n8n analysis workflows."""

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
    """Индексирует nodes по name."""
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


def test_every_analysis_workflow_supports_t_guided_requirements() -> None:
    """PDF, CAD и PDF+CAD одинаково учитывают техническое задание."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        assert "Search Requirements" in nodes, path

        assert "Normalize Requirement Search" in nodes, path

        search = nodes["Search Requirements"]

        serialized = json.dumps(
            search,
            ensure_ascii=False,
        )

        assert "technical_assignment_id" in serialized, path

        assert "/internal/v1/search/technical-assignment-guided" in serialized, path


def test_every_analysis_workflow_passes_n_t_u_separately() -> None:
    """Check Norms получает typed N/T/U data, не общий source bucket."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        check = nodes["Check Norms"]

        serialized = json.dumps(
            check,
            ensure_ascii=False,
        )

        assert "normative_sources" in serialized, path

        assert "technical_assignment_sources" in serialized, path

        assert "conflict_candidates" in serialized, path

        assert "user_package_sources" in serialized, path


def test_every_analysis_workflow_has_finding_local_normative_enrichment() -> None:
    """TZ-5.2 enrichment не должен существовать только в PDF workflow."""
    required = {
        "Prepare Finding Normative Queries",
        "Search Finding Norms",
        "Prepare Experience Queries",
        "Search Experience",
        "Finalize Findings",
    }

    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        names = set(
            _nodes_by_name(
                workflow,
            )
        )

        assert required <= names, path

        finalize = _nodes_by_name(
            workflow,
        )["Finalize Findings"]

        serialized = json.dumps(
            finalize,
            ensure_ascii=False,
        )

        assert "normative_candidates_by_finding" in serialized, path


def test_gpu_dependent_requirement_nodes_retry_transient_errors() -> None:
    """Search Requirements делает bounded retry поверх backend wait policy."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        search = _nodes_by_name(
            workflow,
        )["Search Requirements"]

        assert (
            search.get(
                "retryOnFail",
            )
            is True
        ), path

        assert (
            int(
                search.get(
                    "maxTries",
                    0,
                )
            )
            >= 2
        ), path

        assert (
            int(
                search.get(
                    "waitBetweenTries",
                    0,
                )
            )
            >= 1000
        ), path


def test_requirement_flow_order_is_consistent() -> None:
    """Connections сохраняют одинаковый N/T/U sequence."""
    expected_pairs = (
        (
            "Build Normative Queries",
            "Search Requirements",
        ),
        (
            "Search Requirements",
            "Normalize Requirement Search",
        ),
        (
            "Normalize Requirement Search",
            "Search User Packages",
        ),
        (
            "Search User Packages",
            "Check Norms",
        ),
        (
            "Check Norms",
            "Prepare Finding Normative Queries",
        ),
        (
            "Prepare Finding Normative Queries",
            "Search Finding Norms",
        ),
        (
            "Search Finding Norms",
            "Prepare Experience Queries",
        ),
    )

    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        connections = workflow.get(
            "connections",
            {},
        )

        assert isinstance(
            connections,
            dict,
        )

        for source, target in expected_pairs:
            source_connection = connections[source]

            actual = source_connection["main"][0][0]["node"]

            assert actual == target, (
                path,
                source,
                actual,
                target,
            )
