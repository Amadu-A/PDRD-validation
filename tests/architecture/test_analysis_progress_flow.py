# tests/architecture/test_analysis_progress_flow.py

"""Architecture guards Stage 8.2 analysis progress callbacks."""

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

WORKFLOW_CASES = (
    (
        WORKFLOW_ROOT / "analysis-v2-pdf.json",
        "POST /analysis/v2/pdf",
        (
            (
                "POST /analysis/v2/pdf",
                "Progress Extract Sources",
                "extracting_sources",
            ),
            (
                "Document Extract PDF",
                "Progress Prepare Context",
                "preparing_context",
            ),
            (
                "Expand PDF Pages",
                "Progress Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "building_result",
            ),
        ),
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-cad.json",
        "POST /analysis/v2/cad",
        (
            (
                "POST /analysis/v2/cad",
                "Progress Extract Sources",
                "extracting_sources",
            ),
            (
                "Document Extract CAD",
                "Progress Prepare Context",
                "preparing_context",
            ),
            (
                "Prepare CAD Context",
                "Progress Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "building_result",
            ),
        ),
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        "POST /analysis/v2/pdf-cad",
        (
            (
                "POST /analysis/v2/pdf-cad",
                "Progress Extract Sources",
                "extracting_sources",
            ),
            (
                "Document Extract PDF CAD",
                "Progress Prepare Context",
                "preparing_context",
            ),
            (
                "Prepare Combined Context",
                "Progress Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "building_result",
            ),
        ),
    ),
)


def _workflow(
    path: Path,
) -> dict[str, Any]:
    """Читает committed workflow JSON."""
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
) -> dict[str, dict[str, Any]]:
    """Индексирует nodes по имени."""
    raw_nodes = workflow.get(
        "nodes",
        [],
    )

    assert isinstance(
        raw_nodes,
        list,
    )

    return {
        str(
            node["name"],
        ): node
        for node in raw_nodes
        if isinstance(
            node,
            dict,
        )
        and "name" in node
    }


def _successors(
    workflow: dict[str, Any],
    source: str,
) -> set[str]:
    """Возвращает main successors указанного node."""
    connections = workflow["connections"][source]["main"][0]

    return {
        str(
            connection["node"],
        )
        for connection in connections
    }


def _parameter(
    node: dict[str, Any],
    name: str,
) -> str:
    """Возвращает строковый параметр n8n node."""
    parameters = node.get(
        "parameters",
        {},
    )

    assert isinstance(
        parameters,
        dict,
    )

    value = parameters.get(
        name,
        "",
    )

    return str(
        value,
    )


def test_workflows_publish_real_progress_stages() -> None:
    """Все source modes публикуют одинаковую domain stage vocabulary."""
    for path, webhook_name, cases in WORKFLOW_CASES:
        workflow = _workflow(
            path,
        )

        nodes = _nodes(
            workflow,
        )

        for (
            source_name,
            progress_name,
            stage,
        ) in cases:
            progress_node = nodes[progress_name]

            progress_url = _parameter(
                progress_node,
                "url",
            )

            progress_body = _parameter(
                progress_node,
                "body",
            )

            assert "/internal/v1/analysis-progress/" in progress_url, (
                path,
                progress_name,
            )

            assert webhook_name in progress_url, (
                path,
                progress_name,
            )

            assert f"stage: '{stage}'" in progress_body, (
                path,
                progress_name,
                progress_body,
            )

            assert (
                progress_node.get(
                    "onError",
                )
                == "continueRegularOutput"
            ), (
                path,
                progress_name,
            )

            assert progress_name in _successors(
                workflow,
                source_name,
            ), (
                path,
                source_name,
                progress_name,
            )


def test_progress_callbacks_are_best_effort() -> None:
    """Progress callback не должен останавливать основной analysis pipeline."""
    for path, _webhook_name, cases in WORKFLOW_CASES:
        workflow = _workflow(
            path,
        )

        nodes = _nodes(
            workflow,
        )

        for (
            _source_name,
            progress_name,
            _stage,
        ) in cases:
            progress_node = nodes[progress_name]

            assert (
                progress_node.get(
                    "onError",
                )
                == "continueRegularOutput"
            ), (
                path,
                progress_name,
            )

            assert (
                progress_node.get(
                    "retryOnFail",
                )
                is True
            ), (
                path,
                progress_name,
            )

            assert (
                progress_node.get(
                    "maxTries",
                )
                == 2
            ), (
                path,
                progress_name,
            )
