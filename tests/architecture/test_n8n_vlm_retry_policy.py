# tests/architecture/test_n8n_vlm_retry_policy.py

"""Architecture guards bounded VLM retries in committed n8n workflows."""

import json
from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

_WORKFLOWS = {
    "analysis-v2-pdf.json": {
        "Validate Project Context",
        "Understand Page",
        "Check Technical Assignment",
        "Check Norms",
        "Finalize Findings",
    },
    "analysis-v2-pdf-cad.json": {
        "Validate Project Context",
        "Understand Combined Page",
        "Check Technical Assignment",
        "Check Norms",
        "Finalize Findings",
    },
    "analysis-v2-cad.json": {
        "Understand CAD",
        "Check Technical Assignment",
        "Check Norms",
        "Finalize Findings",
    },
}


def _load_workflow(
    file_name: str,
) -> dict[str, object]:
    """Читает committed workflow JSON."""
    path = REPOSITORY_ROOT / "n8n" / "workflows" / file_name

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def test_all_workflows_have_global_execution_deadline() -> None:
    """n8n завершается раньше Gateway/Application/Celery deadlines."""
    for file_name in _WORKFLOWS:
        workflow = _load_workflow(
            file_name,
        )

        settings = workflow["settings"]

        assert isinstance(
            settings,
            dict,
        )

        assert settings["executionTimeout"] == 1650


def test_expensive_vlm_nodes_never_have_n8n_level_retry() -> None:
    """VLM retry принадлежит adapter, а не n8n orchestration."""
    for (
        file_name,
        expected_names,
    ) in _WORKFLOWS.items():
        workflow = _load_workflow(
            file_name,
        )

        nodes = workflow["nodes"]

        assert isinstance(
            nodes,
            list,
        )

        by_name = {
            node["name"]: node
            for node in nodes
            if isinstance(
                node,
                dict,
            )
            and isinstance(
                node.get(
                    "name",
                ),
                str,
            )
        }

        assert expected_names <= set(
            by_name,
        )

        for node_name in expected_names:
            node = by_name[node_name]

            assert (
                node.get(
                    "retryOnFail",
                    False,
                )
                is False
            )

            assert "maxTries" not in node

            assert "waitBetweenTries" not in node

            parameters = node["parameters"]

            assert isinstance(
                parameters,
                dict,
            )

            options = parameters["options"]

            assert isinstance(
                options,
                dict,
            )

            assert options["timeout"] == 600000


def test_io_search_nodes_keep_local_n8n_retry() -> None:
    """Убирается только дорогой VLM retry, а не полезный I/O retry."""
    for file_name in _WORKFLOWS:
        workflow = _load_workflow(
            file_name,
        )

        nodes = workflow["nodes"]

        assert isinstance(
            nodes,
            list,
        )

        search_requirements = next(
            node
            for node in nodes
            if isinstance(
                node,
                dict,
            )
            and node.get(
                "name",
            )
            == "Search Requirements"
        )

        assert search_requirements["retryOnFail"] is True

        assert search_requirements["maxTries"] == 3
