# ops/apply-analysis-progress.py

"""Добавляет Stage 8.2 progress callbacks в committed n8n workflows."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / "n8n" / "workflows"

STAGES = (
    (
        "Progress Extract Sources",
        "Gate Extract Sources",
        "extracting_sources",
    ),
    (
        "Progress Prepare Context",
        "Gate Prepare Context",
        "preparing_context",
    ),
    (
        "Progress Understand Sheet",
        "Gate Understand Sheet",
        "understanding_sheet",
    ),
    (
        "Progress Retrieve Requirements",
        "Gate Retrieve Requirements",
        "retrieving_requirements",
    ),
    (
        "Progress Check Requirements",
        "Gate Check Requirements",
        "checking_requirements",
    ),
    (
        "Progress Enrich Findings",
        "Gate Enrich Findings",
        "enriching_findings",
    ),
    (
        "Progress Finalize Findings",
        "Gate Finalize Findings",
        "finalizing_findings",
    ),
    (
        "Progress Build Result",
        "Gate Build Result",
        "building_result",
    ),
)

WORKFLOWS = (
    {
        "path": WORKFLOW_ROOT / "analysis-v2-pdf.json",
        "webhook": "POST /analysis/v2/pdf",
        "sources": (
            "POST /analysis/v2/pdf",
            "Resolve Project Context Cache",
            "Expand PDF Pages",
            "Technical Assignment First Pass",
            "Search User Packages",
            "Merge Finding Candidates",
            "Build Experience Map",
            "Finalize Findings",
        ),
        "response_source": "Return PDF Result",
        "response_node": "Respond PDF Result",
        "position_y": 1180,
        "id_prefix": "8a20",
    },
    {
        "path": WORKFLOW_ROOT / "analysis-v2-cad.json",
        "webhook": "POST /analysis/v2/cad",
        "sources": (
            "POST /analysis/v2/cad",
            "Document Extract CAD",
            "Prepare CAD Context",
            "Technical Assignment First Pass",
            "Search User Packages",
            "Merge Finding Candidates",
            "Build Experience Map",
            "Finalize Findings",
        ),
        "response_source": "Build CAD Result",
        "response_node": "Respond CAD Result",
        "position_y": 420,
        "id_prefix": "8b20",
    },
    {
        "path": WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        "webhook": "POST /analysis/v2/pdf-cad",
        "sources": (
            "POST /analysis/v2/pdf-cad",
            "Resolve Project Context Cache",
            "Prepare Combined Context",
            "Technical Assignment First Pass",
            "Search User Packages",
            "Merge Finding Candidates",
            "Build Experience Map",
            "Finalize Findings",
        ),
        "response_source": "Return PDF CAD Result",
        "response_node": "Respond PDF CAD Result",
        "position_y": 420,
        "id_prefix": "8c20",
    },
)


def _load(
    path: Path,
) -> dict[str, Any]:
    """Читает workflow JSON."""
    payload = json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            f"{path} must contain JSON object.",
        )

    return payload


def _node_by_name(
    *,
    workflow: dict[str, Any],
    node_name: str,
) -> dict[str, Any]:
    """Возвращает workflow node по имени."""
    raw_nodes = workflow.get(
        "nodes",
        [],
    )

    if not isinstance(
        raw_nodes,
        list,
    ):
        raise ValueError(
            "Workflow nodes must be list.",
        )

    for node in raw_nodes:
        if (
            isinstance(
                node,
                dict,
            )
            and node.get(
                "name",
            )
            == node_name
        ):
            return node

    raise ValueError(
        f"Missing workflow node: {node_name}",
    )


def _connection(
    target_name: str,
    *,
    input_index: int = 0,
) -> dict[str, Any]:
    """Создаёт стандартное main connection."""
    return {
        "node": target_name,
        "type": "main",
        "index": input_index,
    }


def _main_output(
    *,
    workflow: dict[str, Any],
    source_name: str,
    create: bool,
) -> list[dict[str, Any]]:
    """Возвращает первый main output source node."""
    connections = workflow.setdefault(
        "connections",
        {},
    )

    if not isinstance(
        connections,
        dict,
    ):
        raise ValueError(
            "Workflow connections must be object.",
        )

    source = connections.get(
        source_name,
    )

    if source is None:
        if not create:
            raise ValueError(
                f"Missing source connection: {source_name}",
            )

        source = {
            "main": [
                [],
            ],
        }

        connections[source_name] = source

    if not isinstance(
        source,
        dict,
    ):
        raise ValueError(
            f"Invalid source connection: {source_name}",
        )

    main = source.get(
        "main",
    )

    if (
        not isinstance(
            main,
            list,
        )
        or not main
        or not isinstance(
            main[0],
            list,
        )
    ):
        if not create:
            raise ValueError(
                f"Invalid main connection: {source_name}",
            )

        source["main"] = [
            [],
        ]

        main = source["main"]

    return main[0]


def _progress_node(
    *,
    name: str,
    stage: str,
    webhook: str,
    node_id: str,
    position_x: int,
    position_y: int,
) -> dict[str, Any]:
    """Строит один best-effort progress callback на stage."""
    body = f"={{{{ JSON.stringify({{ stage: '{stage}' }}) }}}}"

    url = (
        "={{ "
        "'http://api-gateway:8000/"
        "internal/v1/analysis-progress/' + "
        f"$('{webhook}').first().json.body.document_id"
        " }}"
    )

    return {
        "parameters": {
            "method": "POST",
            "url": url,
            "sendBody": True,
            "contentType": "raw",
            "rawContentType": "application/json",
            "body": body,
            "options": {
                "timeout": 3000,
            },
        },
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [
            position_x,
            position_y,
        ],
        "executeOnce": True,
        "retryOnFail": False,
        "onError": "continueRegularOutput",
    }


def _gate_node(
    *,
    name: str,
    node_id: str,
    position_x: int,
    position_y: int,
) -> dict[str, Any]:
    """Строит Merge gate, который ждёт callback и сохраняет source data."""
    return {
        "parameters": {
            "mode": "chooseBranch",
            "useDataOfInput": 1,
        },
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.merge",
        "typeVersion": 3.2,
        "position": [
            position_x,
            position_y,
        ],
    }


def _respond_node(
    *,
    name: str,
    node_id: str,
    position: list[int],
) -> dict[str, Any]:
    """Строит явный terminal webhook response node."""
    return {
        "parameters": {
            "respondWith": "firstIncomingItem",
            "options": {},
        },
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.respondToWebhook",
        "typeVersion": 1.5,
        "position": position,
    }


def _normalize_progress_node(
    *,
    node: dict[str, Any],
    name: str,
    stage: str,
    webhook: str,
) -> None:
    """Нормализует ранее созданный progress node."""
    current_position = node.get(
        "position",
        [
            0,
            0,
        ],
    )

    current_id = str(
        node.get(
            "id",
            "",
        )
    )

    normalized = _progress_node(
        name=name,
        stage=stage,
        webhook=webhook,
        node_id=current_id,
        position_x=int(
            current_position[0],
        ),
        position_y=int(
            current_position[1],
        ),
    )

    node["parameters"] = normalized["parameters"]
    node["type"] = normalized["type"]
    node["typeVersion"] = normalized["typeVersion"]
    node["executeOnce"] = True
    node["retryOnFail"] = False
    node["onError"] = "continueRegularOutput"

    node.pop(
        "maxTries",
        None,
    )

    node.pop(
        "waitBetweenTries",
        None,
    )


def _normalize_gate_node(
    *,
    node: dict[str, Any],
    name: str,
) -> None:
    """Нормализует Merge gate без изменения его позиции и id."""
    current_position = node.get(
        "position",
        [
            0,
            0,
        ],
    )

    current_id = str(
        node.get(
            "id",
            "",
        )
    )

    normalized = _gate_node(
        name=name,
        node_id=current_id,
        position_x=int(
            current_position[0],
        ),
        position_y=int(
            current_position[1],
        ),
    )

    node["parameters"] = normalized["parameters"]
    node["type"] = normalized["type"]
    node["typeVersion"] = normalized["typeVersion"]


def _remove_target_from_other_sources(
    *,
    workflow: dict[str, Any],
    target_name: str,
    keep_sources: frozenset[str],
) -> None:
    """Удаляет старые side-branch подключения target node."""
    connections = workflow.get(
        "connections",
        {},
    )

    if not isinstance(
        connections,
        dict,
    ):
        raise ValueError(
            "Workflow connections must be object.",
        )

    for source_name, source in connections.items():
        if source_name in keep_sources or not isinstance(
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

            output[:] = [
                item
                for item in output
                if not (
                    isinstance(
                        item,
                        dict,
                    )
                    and item.get(
                        "node",
                    )
                    == target_name
                )
            ]


def _ensure_progress_gate(
    *,
    workflow: dict[str, Any],
    source_name: str,
    progress_name: str,
    gate_name: str,
) -> None:
    """Делает callback barrier без потери source data.

    Source передаёт исходные items в Merge Input 1 и параллельно запускает
    progress callback. Callback приходит в Merge Input 2.

    Merge ждёт обе ветки и выпускает неизменённые Input 1 items дальше.
    Поэтому downstream не стартует до callback, но HTTP response progress
    node не подменяет данные analysis pipeline.
    """
    _remove_target_from_other_sources(
        workflow=workflow,
        target_name=progress_name,
        keep_sources=frozenset(
            {
                source_name,
            }
        ),
    )

    _remove_target_from_other_sources(
        workflow=workflow,
        target_name=gate_name,
        keep_sources=frozenset(
            {
                source_name,
                progress_name,
            }
        ),
    )

    source_output = _main_output(
        workflow=workflow,
        source_name=source_name,
        create=False,
    )

    downstream: list[
        dict[
            str,
            Any,
        ]
    ] = []

    for item in source_output:
        if not isinstance(
            item,
            dict,
        ):
            raise ValueError(
                f"Invalid connection in {source_name}.",
            )

        if item.get(
            "node",
        ) in {
            progress_name,
            gate_name,
        }:
            continue

        downstream.append(
            dict(
                item,
            )
        )

    gate_output = _main_output(
        workflow=workflow,
        source_name=gate_name,
        create=True,
    )

    if downstream:
        gate_output[:] = downstream

    elif not gate_output:
        raise ValueError(
            "Progress gate не может потерять "
            "downstream successor: "
            f"{source_name} -> {gate_name}.",
        )

    source_output[:] = [
        _connection(
            progress_name,
        ),
        _connection(
            gate_name,
        ),
    ]

    progress_output = _main_output(
        workflow=workflow,
        source_name=progress_name,
        create=True,
    )

    progress_output[:] = [
        _connection(
            gate_name,
            input_index=1,
        )
    ]


def _ensure_explicit_response(
    *,
    workflow: dict[str, Any],
    webhook_name: str,
    response_source: str,
    response_name: str,
    response_id: str,
) -> None:
    """Переводит webhook на explicit Respond to Webhook."""
    webhook_node = _node_by_name(
        workflow=workflow,
        node_name=webhook_name,
    )

    parameters = webhook_node.get(
        "parameters",
    )

    if not isinstance(
        parameters,
        dict,
    ):
        raise ValueError(
            f"{webhook_name}: parameters must be object.",
        )

    parameters["responseMode"] = "responseNode"

    raw_nodes = workflow.get(
        "nodes",
        [],
    )

    if not isinstance(
        raw_nodes,
        list,
    ):
        raise ValueError(
            "Workflow nodes must be list.",
        )

    response_node: (
        dict[
            str,
            Any,
        ]
        | None
    ) = None

    for node in raw_nodes:
        if (
            isinstance(
                node,
                dict,
            )
            and node.get(
                "name",
            )
            == response_name
        ):
            response_node = node
            break

    response_source_node = _node_by_name(
        workflow=workflow,
        node_name=response_source,
    )

    source_position = response_source_node.get(
        "position",
        [
            0,
            0,
        ],
    )

    response_position = [
        int(
            source_position[0],
        )
        + 240,
        int(
            source_position[1],
        ),
    ]

    normalized_response = _respond_node(
        name=response_name,
        node_id=response_id,
        position=response_position,
    )

    if response_node is None:
        raw_nodes.append(
            normalized_response,
        )

    else:
        response_node["parameters"] = normalized_response["parameters"]

        response_node["type"] = normalized_response["type"]

        response_node["typeVersion"] = normalized_response["typeVersion"]

    output = _main_output(
        workflow=workflow,
        source_name=response_source,
        create=True,
    )

    unrelated = [
        item
        for item in output
        if (
            isinstance(
                item,
                dict,
            )
            and item.get(
                "node",
            )
            != response_name
        )
    ]

    if unrelated:
        raise ValueError(
            "Result node уже имеет неожиданный "
            "successor: "
            f"{response_source}: {unrelated}",
        )

    output[:] = [
        _connection(
            response_name,
        )
    ]


def _ensure_node(
    *,
    raw_nodes: list[Any],
    name: str,
    factory: dict[str, Any],
) -> dict[str, Any]:
    """Возвращает существующий node либо добавляет новый."""
    for node in raw_nodes:
        if (
            isinstance(
                node,
                dict,
            )
            and node.get(
                "name",
            )
            == name
        ):
            return node

    raw_nodes.append(
        factory,
    )

    return factory


def _apply(
    config: dict[
        str,
        Any,
    ],
) -> None:
    """Применяет Stage 8.2 progress barrier wiring к workflow."""
    path = config["path"]

    workflow = _load(
        path,
    )

    raw_nodes = workflow.get(
        "nodes",
    )

    if not isinstance(
        raw_nodes,
        list,
    ):
        raise ValueError(
            f"{path}: nodes must be list.",
        )

    node_names = {
        str(
            node.get(
                "name",
                "",
            )
        )
        for node in raw_nodes
        if isinstance(
            node,
            dict,
        )
    }

    required_names = (
        *config["sources"],
        config["webhook"],
        config["response_source"],
    )

    for node_name in required_names:
        if node_name not in node_names:
            raise ValueError(
                f"{path}: missing node {node_name}",
            )

    for index, (
        progress_name,
        gate_name,
        stage,
    ) in enumerate(
        STAGES,
        start=1,
    ):
        position_x = -2200 + index * 420

        progress_node = _ensure_node(
            raw_nodes=raw_nodes,
            name=progress_name,
            factory=_progress_node(
                name=progress_name,
                stage=stage,
                webhook=config["webhook"],
                node_id=(f"{config['id_prefix']}0000-0000-4000-8000-{index:012d}"),
                position_x=position_x,
                position_y=config["position_y"],
            ),
        )

        _normalize_progress_node(
            node=progress_node,
            name=progress_name,
            stage=stage,
            webhook=config["webhook"],
        )

        gate_node = _ensure_node(
            raw_nodes=raw_nodes,
            name=gate_name,
            factory=_gate_node(
                name=gate_name,
                node_id=(
                    f"{config['id_prefix']}0000-0000-4000-8000-{100 + index:012d}"
                ),
                position_x=(position_x + 210),
                position_y=(config["position_y"] + 160),
            ),
        )

        _normalize_gate_node(
            node=gate_node,
            name=gate_name,
        )

        _ensure_progress_gate(
            workflow=workflow,
            source_name=(config["sources"][index - 1]),
            progress_name=progress_name,
            gate_name=gate_name,
        )

    _ensure_explicit_response(
        workflow=workflow,
        webhook_name=config["webhook"],
        response_source=config["response_source"],
        response_name=config["response_node"],
        response_id=(f"{config['id_prefix']}0000-0000-4000-8000-000000000009"),
    )

    path.write_text(
        json.dumps(
            workflow,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"updated: {path.relative_to(ROOT)}",
    )


def main() -> None:
    """Обновляет все три analysis workflow."""
    for config in WORKFLOWS:
        _apply(
            config,
        )


if __name__ == "__main__":
    main()
