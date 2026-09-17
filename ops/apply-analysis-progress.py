# ops/apply-analysis-progress.py

"""Добавляет Stage 8.2 progress callbacks в committed n8n workflows."""

import json
from pathlib import Path
from typing import Any

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[1]
)

WORKFLOW_ROOT = ROOT / "n8n" / "workflows"

STAGES = (
    (
        "Progress Extract Sources",
        "extracting_sources",
    ),
    (
        "Progress Prepare Context",
        "preparing_context",
    ),
    (
        "Progress Understand Sheet",
        "understanding_sheet",
    ),
    (
        "Progress Retrieve Requirements",
        "retrieving_requirements",
    ),
    (
        "Progress Check Requirements",
        "checking_requirements",
    ),
    (
        "Progress Enrich Findings",
        "enriching_findings",
    ),
    (
        "Progress Finalize Findings",
        "finalizing_findings",
    ),
    (
        "Progress Build Result",
        "building_result",
    ),
)

WORKFLOWS = (
    {
        "path": (WORKFLOW_ROOT / "analysis-v2-pdf.json"),
        "webhook": "POST /analysis/v2/pdf",
        "sources": (
            "POST /analysis/v2/pdf",
            "Document Extract PDF",
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
        "path": (WORKFLOW_ROOT / "analysis-v2-cad.json"),
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
        "path": (WORKFLOW_ROOT / "analysis-v2-pdf-cad.json"),
        "webhook": "POST /analysis/v2/pdf-cad",
        "sources": (
            "POST /analysis/v2/pdf-cad",
            "Document Extract PDF CAD",
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
        if not isinstance(
            node,
            dict,
        ):
            continue

        if (
            node.get(
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
) -> dict[str, Any]:
    """Создаёт стандартное main connection."""
    return {
        "node": target_name,
        "type": "main",
        "index": 0,
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
    """Строит non-blocking best-effort progress callback."""
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
            "rawContentType": ("application/json"),
            "body": body,
            "options": {
                "timeout": 3000,
            },
        },
        "id": node_id,
        "name": name,
        "type": ("n8n-nodes-base.httpRequest"),
        "typeVersion": 4.2,
        "position": [
            position_x,
            position_y,
        ],
        "retryOnFail": False,
        "onError": "continueRegularOutput",
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
            "respondWith": ("firstIncomingItem"),
            "options": {},
        },
        "id": node_id,
        "name": name,
        "type": ("n8n-nodes-base.respondToWebhook"),
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


def _ensure_progress_first(
    *,
    workflow: dict[str, Any],
    source_name: str,
    progress_name: str,
) -> None:
    """Ставит progress callback первым successor.

    Для executionOrder=v1 callback должен завершиться
    до перехода в основной тяжёлый analysis branch.
    """
    output = _main_output(
        workflow=workflow,
        source_name=source_name,
        create=False,
    )

    existing_progress: (
        dict[
            str,
            Any,
        ]
        | None
    ) = None

    remaining: list[
        dict[
            str,
            Any,
        ]
    ] = []

    for item in output:
        if not isinstance(
            item,
            dict,
        ):
            raise ValueError(
                f"Invalid connection in {source_name}.",
            )

        if (
            item.get(
                "node",
            )
            == progress_name
        ):
            if existing_progress is None:
                existing_progress = item

            continue

        remaining.append(
            item,
        )

    if existing_progress is None:
        existing_progress = _connection(
            progress_name,
        )

    if not remaining:
        raise ValueError(
            f"Progress callback не должен быть единственным successor: {source_name}.",
        )

    output[:] = [
        existing_progress,
        *remaining,
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
            "Result node уже имеет неожиданный successor: "
            f"{response_source}: {unrelated}",
        )

    output[:] = [
        _connection(
            response_name,
        ),
    ]


def _apply(
    config: dict[str, Any],
) -> None:
    """Применяет Stage 8.2 wiring к одному workflow."""
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
        stage,
    ) in enumerate(
        STAGES,
        start=1,
    ):
        existing_node: (
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
                == progress_name
            ):
                existing_node = node
                break

        if existing_node is None:
            existing_node = _progress_node(
                name=progress_name,
                stage=stage,
                webhook=config["webhook"],
                node_id=(f"{config['id_prefix']}0000-0000-4000-8000-{index:012d}"),
                position_x=(-2200 + index * 420),
                position_y=config["position_y"],
            )

            raw_nodes.append(
                existing_node,
            )

        else:
            _normalize_progress_node(
                node=existing_node,
                name=progress_name,
                stage=stage,
                webhook=config["webhook"],
            )

        source_name = config["sources"][index - 1]

        _ensure_progress_first(
            workflow=workflow,
            source_name=source_name,
            progress_name=progress_name,
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
