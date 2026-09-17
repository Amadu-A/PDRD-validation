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


def _progress_node(
    *,
    name: str,
    stage: str,
    webhook: str,
    node_id: str,
    position_x: int,
    position_y: int,
) -> dict[str, Any]:
    """Строит best-effort HTTP progress callback."""
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
                "timeout": 30000,
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
        "retryOnFail": True,
        "maxTries": 2,
        "waitBetweenTries": 500,
        "onError": ("continueRegularOutput"),
    }


def _ensure_connection(
    *,
    workflow: dict[str, Any],
    source_name: str,
    target_name: str,
) -> None:
    """Добавляет side branch без изменения existing successor."""
    connections = workflow.setdefault(
        "connections",
        {},
    )

    source = connections.get(
        source_name,
    )

    if not isinstance(
        source,
        dict,
    ):
        raise ValueError(
            f"Missing source connection: {source_name}",
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
        raise ValueError(
            f"Invalid main connection: {source_name}",
        )

    if any(
        connection.get(
            "node",
        )
        == target_name
        for connection in main[0]
        if isinstance(
            connection,
            dict,
        )
    ):
        return

    main[0].append(
        {
            "node": target_name,
            "type": "main",
            "index": 0,
        }
    )


def _apply(
    config: dict[str, Any],
) -> None:
    """Добавляет callbacks в один workflow."""
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

    for source_name in config["sources"]:
        if source_name not in node_names:
            raise ValueError(
                f"{path}: missing node {source_name}",
            )

    for index, (
        progress_name,
        stage,
    ) in enumerate(
        STAGES,
        start=1,
    ):
        if progress_name not in node_names:
            raw_nodes.append(
                _progress_node(
                    name=progress_name,
                    stage=stage,
                    webhook=config["webhook"],
                    node_id=(f"{config['id_prefix']}0000-0000-4000-8000-{index:012d}"),
                    position_x=(-2200 + index * 420),
                    position_y=config["position_y"],
                )
            )

        source_name = config["sources"][index - 1]

        _ensure_connection(
            workflow=workflow,
            source_name=source_name,
            target_name=progress_name,
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
