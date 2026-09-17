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
    {
        "path": (WORKFLOW_ROOT / "analysis-v2-pdf.json"),
        "webhook": "POST /analysis/v2/pdf",
        "response_source": "Return PDF Result",
        "response_node": "Respond PDF Result",
        "cases": (
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
    },
    {
        "path": (WORKFLOW_ROOT / "analysis-v2-cad.json"),
        "webhook": "POST /analysis/v2/cad",
        "response_source": "Build CAD Result",
        "response_node": "Respond CAD Result",
        "cases": (
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
    },
    {
        "path": (WORKFLOW_ROOT / "analysis-v2-pdf-cad.json"),
        "webhook": "POST /analysis/v2/pdf-cad",
        "response_source": "Return PDF CAD Result",
        "response_node": "Respond PDF CAD Result",
        "cases": (
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
    },
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
        if (
            isinstance(
                node,
                dict,
            )
            and "name" in node
        )
    }


def _successors_in_order(
    workflow: dict[str, Any],
    source: str,
) -> list[str]:
    """Возвращает main successors с сохранением execution order."""
    connections = workflow["connections"][source]["main"][0]

    return [
        str(
            connection["node"],
        )
        for connection in connections
    ]


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
    """Все source modes публикуют одинаковую stage vocabulary."""
    for config in WORKFLOW_CASES:
        workflow = _workflow(
            config["path"],
        )

        nodes = _nodes(
            workflow,
        )

        for (
            source_name,
            progress_name,
            stage,
        ) in config["cases"]:
            progress_node = nodes[progress_name]

            progress_url = _parameter(
                progress_node,
                "url",
            )

            progress_body = _parameter(
                progress_node,
                "body",
            )

            assert "/internal/v1/analysis-progress/" in progress_url

            assert config["webhook"] in progress_url

            assert f"stage: '{stage}'" in progress_body

            successors = _successors_in_order(
                workflow,
                source_name,
            )

            assert successors[0] == progress_name, (
                config["path"],
                source_name,
                successors,
            )

            assert (
                len(
                    successors,
                )
                >= 2
            ), (
                config["path"],
                source_name,
                successors,
            )


def test_progress_callbacks_are_non_blocking_best_effort() -> None:
    """Progress failure не должен заметно задерживать analysis path."""
    for config in WORKFLOW_CASES:
        workflow = _workflow(
            config["path"],
        )

        nodes = _nodes(
            workflow,
        )

        for (
            _source_name,
            progress_name,
            _stage,
        ) in config["cases"]:
            progress_node = nodes[progress_name]

            assert (
                progress_node.get(
                    "onError",
                )
                == "continueRegularOutput"
            )

            assert (
                progress_node.get(
                    "retryOnFail",
                )
                is False
            )

            parameters = progress_node["parameters"]

            assert parameters["options"]["timeout"] == 3000


def test_webhook_response_is_explicit_and_not_last_node_dependent() -> None:
    """Side-effect progress node не может стать HTTP response workflow."""
    for config in WORKFLOW_CASES:
        workflow = _workflow(
            config["path"],
        )

        nodes = _nodes(
            workflow,
        )

        webhook = nodes[config["webhook"]]

        parameters = webhook["parameters"]

        assert parameters["responseMode"] == "responseNode"

        response_name = config["response_node"]

        response_node = nodes[response_name]

        assert response_node["type"] == "n8n-nodes-base.respondToWebhook"

        assert response_node["parameters"]["respondWith"] == "firstIncomingItem"

        assert _successors_in_order(
            workflow,
            config["response_source"],
        ) == [
            response_name,
        ]


def test_workflows_use_v1_execution_order() -> None:
    """Progress-first branch ordering имеет deterministic v1 semantics."""
    for config in WORKFLOW_CASES:
        workflow = _workflow(
            config["path"],
        )

        assert workflow["settings"]["executionOrder"] == "v1"
