# tests/architecture/test_analysis_progress_flow.py

"""Architecture guards Stage 8.3 progress and cooperative cancellation."""

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
                "Continue Extract Sources",
                "Gate Extract Sources",
                "extracting_sources",
            ),
            (
                "Resolve Project Context Cache",
                "Progress Prepare Context",
                "Continue Prepare Context",
                "Gate Prepare Context",
                "preparing_context",
            ),
            (
                "Expand PDF Pages",
                "Progress Understand Sheet",
                "Continue Understand Sheet",
                "Gate Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "Continue Retrieve Requirements",
                "Gate Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "Continue Check Requirements",
                "Gate Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "Continue Enrich Findings",
                "Gate Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "Continue Finalize Findings",
                "Gate Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "Continue Build Result",
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
                "Continue Extract Sources",
                "Gate Extract Sources",
                "extracting_sources",
            ),
            (
                "Document Extract CAD",
                "Progress Prepare Context",
                "Continue Prepare Context",
                "Gate Prepare Context",
                "preparing_context",
            ),
            (
                "Prepare CAD Context",
                "Progress Understand Sheet",
                "Continue Understand Sheet",
                "Gate Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "Continue Retrieve Requirements",
                "Gate Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "Continue Check Requirements",
                "Gate Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "Continue Enrich Findings",
                "Gate Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "Continue Finalize Findings",
                "Gate Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "Continue Build Result",
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
                "Continue Extract Sources",
                "Gate Extract Sources",
                "extracting_sources",
            ),
            (
                "Resolve Project Context Cache",
                "Progress Prepare Context",
                "Continue Prepare Context",
                "Gate Prepare Context",
                "preparing_context",
            ),
            (
                "Prepare Combined Context",
                "Progress Understand Sheet",
                "Continue Understand Sheet",
                "Gate Understand Sheet",
                "understanding_sheet",
            ),
            (
                "Technical Assignment First Pass",
                "Progress Retrieve Requirements",
                "Continue Retrieve Requirements",
                "Gate Retrieve Requirements",
                "retrieving_requirements",
            ),
            (
                "Search User Packages",
                "Progress Check Requirements",
                "Continue Check Requirements",
                "Gate Check Requirements",
                "checking_requirements",
            ),
            (
                "Merge Finding Candidates",
                "Progress Enrich Findings",
                "Continue Enrich Findings",
                "Gate Enrich Findings",
                "enriching_findings",
            ),
            (
                "Build Experience Map",
                "Progress Finalize Findings",
                "Continue Finalize Findings",
                "Gate Finalize Findings",
                "finalizing_findings",
            ),
            (
                "Finalize Findings",
                "Progress Build Result",
                "Continue Build Result",
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


def _main_output_connections(
    workflow: dict[str, Any],
    source: str,
    output_index: int,
) -> list[dict[str, Any]]:
    """Возвращает указанный main output source node."""
    main = workflow["connections"][source]["main"]

    assert isinstance(
        main,
        list,
    )

    assert output_index < len(
        main,
    )

    connections = main[output_index]

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


def _main_connections(
    workflow: dict[str, Any],
    source: str,
) -> list[dict[str, Any]]:
    """Возвращает первый main output source node."""
    return _main_output_connections(
        workflow,
        source,
        0,
    )


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


def test_workflows_publish_progress_through_cancellation_gate() -> None:
    """Callback завершает checkpoint до downstream и умеет остановить pipeline."""
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
            continue_name,
            gate_name,
            stage,
        ) in config["cases"]:
            progress_node = nodes[progress_name]
            continue_node = nodes[continue_name]
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

            assert _successors_in_order(
                workflow,
                progress_name,
            ) == [
                continue_name,
            ]

            assert continue_node["type"] == "n8n-nodes-base.if"
            assert continue_node["typeVersion"] == 2.2

            continue_parameters = continue_node["parameters"]
            conditions = continue_parameters["conditions"]["conditions"]

            assert (
                len(
                    conditions,
                )
                == 1
            )

            condition = conditions[0]

            assert condition["leftValue"] == ("={{ String($json.cancelled ?? false) }}")
            assert condition["rightValue"] == "true"
            assert condition["operator"] == {
                "type": "string",
                "operation": "notEquals",
            }

            continue_output = _main_output_connections(
                workflow,
                continue_name,
                0,
            )

            assert continue_output == [
                {
                    "node": gate_name,
                    "type": "main",
                    "index": 1,
                }
            ]

            cancellation_output = _main_output_connections(
                workflow,
                continue_name,
                1,
            )

            assert cancellation_output == [
                {
                    "node": "Build Cancelled Result",
                    "type": "main",
                    "index": 0,
                }
            ]

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
                continue_name,
            ) == {
                (
                    progress_name,
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
                    continue_name,
                    1,
                ),
            }


def test_progress_callbacks_remain_single_best_effort_requests() -> None:
    """Infrastructure сбой progress API сам по себе не отменяет analysis."""
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
            _continue_name,
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


def test_cancelled_checkpoint_returns_explicit_webhook_response() -> None:
    """Все checkpoint false branches завершаются единым cancelled response."""
    for config in WORKFLOW_CASES:
        workflow = _workflow(
            config["path"],
        )

        nodes = _nodes(
            workflow,
        )

        build_cancelled = nodes["Build Cancelled Result"]
        respond_cancelled = nodes["Respond Cancelled"]

        assert build_cancelled["type"] == "n8n-nodes-base.code"

        code = _parameter(
            build_cancelled,
            "jsCode",
        )

        assert "status: 'cancelled'" in code
        assert "reason: 'analysis_cancelled'" in code

        expected_sources = {
            (
                continue_name,
                0,
            )
            for (
                _source_name,
                _progress_name,
                continue_name,
                _gate_name,
                _stage,
            ) in config["cases"]
        }

        assert (
            _incoming_connections(
                workflow,
                "Build Cancelled Result",
            )
            == expected_sources
        )

        assert _successors_in_order(
            workflow,
            "Build Cancelled Result",
        ) == [
            "Respond Cancelled",
        ]

        assert respond_cancelled["type"] == "n8n-nodes-base.respondToWebhook"
        assert respond_cancelled["parameters"]["respondWith"] == ("firstIncomingItem")


def test_pdf_progress_keeps_stage7_visualization_persistence_order() -> None:
    """Cancellation migration не разрывает Stage 7 extract -> persist -> cache."""
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


def test_completed_webhook_response_remains_explicit() -> None:
    """Cancellation branch не меняет успешный HTTP response workflow."""
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
