# tests/architecture/test_analysis_progress_flow.py

"""Architecture guards Stage 8.2 analysis progress callbacks."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / "n8n" / "workflows"

WORKFLOW_CASES = (
    {
        "path": WORKFLOW_ROOT / "analysis-v2-pdf.json",
        "webhook": "POST /analysis/v2/pdf",
        "response_source": "Return PDF Result",
        "response_node": "Respond PDF Result",
        "cases": (
            (
                "POST /analysis/v2/pdf",
                "Progress Extract Sources",
                "Gate Extract Sources",
                "extracting_sources",
            ),
            (
                "Resolve Project Context Cache",
                "Progress Prepare Context",
                "Gate Prepare Context",
                "preparing_context",
            ),
            (
                "Expand PDF Pages",
                "Progress Understand Sheet",
                "Gate Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "Gate Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "Gate Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "Gate Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "Gate Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "Gate Build Result",
                "building_result",
            ),
        ),
    },
    {
        "path": WORKFLOW_ROOT / "analysis-v2-cad.json",
        "webhook": "POST /analysis/v2/cad",
        "response_source": "Build CAD Result",
        "response_node": "Respond CAD Result",
        "cases": (
            (
                "POST /analysis/v2/cad",
                "Progress Extract Sources",
                "Gate Extract Sources",
                "extracting_sources",
            ),
            (
                "Document Extract CAD",
                "Progress Prepare Context",
                "Gate Prepare Context",
                "preparing_context",
            ),
            (
                "Prepare CAD Context",
                "Progress Understand Sheet",
                "Gate Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "Gate Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "Gate Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "Gate Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "Gate Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "Gate Build Result",
                "building_result",
            ),
        ),
    },
    {
        "path": WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        "webhook": "POST /analysis/v2/pdf-cad",
        "response_source": "Return PDF CAD Result",
        "response_node": "Respond PDF CAD Result",
        "cases": (
            (
                "POST /analysis/v2/pdf-cad",
                "Progress Extract Sources",
                "Gate Extract Sources",
                "extracting_sources",
            ),
            (
                "Resolve Project Context Cache",
                "Progress Prepare Context",
                "Gate Prepare Context",
                "preparing_context",
            ),
            (
                "Prepare Combined Context",
                "Progress Understand Sheet",
                "Gate Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "Gate Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "Gate Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "Gate Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "Gate Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "Gate Build Result",
                "building_result",
            ),
        ),
    },
)

PDF_VISUALIZATION_PATHS = (
    (
        WORKFLOW_ROOT / "analysis-v2-pdf.json",
        "Document Extract PDF",
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        "Document Extract PDF CAD",
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
        if (
            isinstance(
                node,
                dict,
            )
            and "name" in node
        )
    }


def _main_connections(
    workflow: dict[str, Any],
    source: str,
) -> list[dict[str, Any]]:
    """Возвращает первый main output source node."""
    connections = workflow["connections"][source]["main"][0]

    assert isinstance(
        connections,
        list,
    )

    assert all(
        isinstance(
            connection,
            dict,
        )
        for connection in connections
    )

    return connections


def _successors_in_order(
    workflow: dict[str, Any],
    source: str,
) -> list[str]:
    """Возвращает main successors с сохранением execution order."""
    return [
        str(connection["node"])
        for connection in _main_connections(
            workflow,
            source,
        )
    ]


def _incoming_connections(
    workflow: dict[str, Any],
    target: str,
) -> set[
    tuple[
        str,
        int,
    ]
]:
    """Возвращает source node и target input index для всех входов target."""
    result: set[
        tuple[
            str,
            int,
        ]
    ] = set()

    raw_connections = workflow.get(
        "connections",
        {},
    )

    assert isinstance(
        raw_connections,
        dict,
    )

    for source_name, source in raw_connections.items():
        if not isinstance(
            source,
            dict,
        ):
            continue

        main = source.get(
            "main",
            [],
        )

        if not isinstance(
            main,
            list,
        ):
            continue

        for output in main:
            if not isinstance(
                output,
                list,
            ):
                continue

            for connection in output:
                if not isinstance(
                    connection,
                    dict,
                ):
                    continue

                if (
                    connection.get(
                        "node",
                    )
                    != target
                ):
                    continue

                result.add(
                    (
                        str(
                            source_name,
                        ),
                        int(
                            connection.get(
                                "index",
                                0,
                            )
                        ),
                    )
                )

    return result


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

    return str(
        parameters.get(
            name,
            "",
        )
    )


def test_workflows_publish_progress_through_blocking_data_preserving_gate() -> None:
    """Callback завершается до downstream без подмены source data."""
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
            gate_name,
            stage,
        ) in config["cases"]:
            progress_node = nodes[progress_name]

            gate_node = nodes[gate_name]

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

            assert _successors_in_order(
                workflow,
                source_name,
            ) == [
                progress_name,
                gate_name,
            ], (
                config["path"],
                source_name,
            )

            progress_connections = _main_connections(
                workflow,
                progress_name,
            )

            assert (
                len(
                    progress_connections,
                )
                == 1
            )

            assert progress_connections[0]["node"] == gate_name

            assert progress_connections[0]["index"] == 1

            gate_parameters = gate_node["parameters"]

            assert gate_node["type"] == "n8n-nodes-base.merge"

            assert gate_node["typeVersion"] == 3.2

            assert gate_parameters["mode"] == "chooseBranch"

            assert gate_parameters["useDataOfInput"] == 1

            assert _main_connections(
                workflow,
                gate_name,
            )

            assert _incoming_connections(
                workflow,
                progress_name,
            ) == {
                (
                    source_name,
                    0,
                ),
            }

            assert _incoming_connections(
                workflow,
                gate_name,
            ) == {
                (
                    source_name,
                    0,
                ),
                (
                    progress_name,
                    1,
                ),
            }


def test_progress_callbacks_are_single_best_effort_requests() -> None:
    """Stage callback выполняется один раз и не валит analysis при сбое."""
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
            _gate_name,
            _stage,
        ) in config["cases"]:
            progress_node = nodes[progress_name]

            assert (
                progress_node.get(
                    "executeOnce",
                )
                is True
            )

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


def test_pdf_progress_keeps_stage7_visualization_persistence_order() -> None:
    """Progress migration не разрывает Stage 7 extract -> persist -> cache."""
    for (
        path,
        extract_name,
    ) in PDF_VISUALIZATION_PATHS:
        workflow = _workflow(
            path,
        )

        assert _successors_in_order(
            workflow,
            extract_name,
        ) == [
            "Persist Visualization Artifact",
        ], path

        assert _successors_in_order(
            workflow,
            "Persist Visualization Artifact",
        ) == [
            "Resolve Project Context Cache",
        ], path


def test_webhook_response_is_explicit_and_not_last_node_dependent() -> None:
    """Progress/gate nodes не могут стать HTTP response workflow."""
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
    """Committed workflows сохраняют deterministic v1 semantics."""
    for config in WORKFLOW_CASES:
        workflow = _workflow(
            config["path"],
        )

        assert workflow["settings"]["executionOrder"] == "v1"
