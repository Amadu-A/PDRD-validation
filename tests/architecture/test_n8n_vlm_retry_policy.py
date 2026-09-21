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
        "Validate Project Context": 600_000,
        "Understand Pages Stage": 3_300_000,
        "Check Technical Assignment": 3_300_000,
        "Check Norms": 3_300_000,
        "Finalize Findings": 3_300_000,
    },
    "analysis-v2-pdf-cad.json": {
        "Validate Project Context": 600_000,
        "Understand Combined Page": 600_000,
        "Check Technical Assignment": 600_000,
        "Check Norms": 600_000,
        "Finalize Findings": 600_000,
    },
    "analysis-v2-cad.json": {
        "Understand CAD": 600_000,
        "Check Technical Assignment": 600_000,
        "Check Norms": 600_000,
        "Finalize Findings": 600_000,
    },
}


def _load_workflow(
    file_name: str,
) -> dict[str, object]:
    """Читает committed n8n workflow."""
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

        assert settings["executionTimeout"] == 3450


def test_expensive_vlm_nodes_never_have_n8n_level_retry() -> None:
    """VLM retry принадлежит adapter, а не n8n orchestration."""
    for (
        file_name,
        expected_nodes,
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

        assert set(
            expected_nodes,
        ) <= set(
            by_name,
        )

        for (
            node_name,
            expected_timeout,
        ) in expected_nodes.items():
            node = by_name[node_name]

            assert (
                node.get(
                    "retryOnFail",
                    False,
                )
                is False
            ), (
                file_name,
                node_name,
            )

            assert "maxTries" not in node, (
                file_name,
                node_name,
            )

            assert "waitBetweenTries" not in node, (
                file_name,
                node_name,
            )

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

            assert options["timeout"] == expected_timeout, (
                file_name,
                node_name,
            )


def test_pdf_document_scoped_gpu_stages_have_longer_http_budget() -> None:
    """Long PDF stage может законно держать GPU дольше 10 минут."""
    expected = _WORKFLOWS["analysis-v2-pdf.json"]

    assert expected["Understand Pages Stage"] == 3_300_000

    assert expected["Check Technical Assignment"] == 3_300_000

    assert expected["Check Norms"] == 3_300_000

    assert expected["Finalize Findings"] == 3_300_000

    # Project Context остаётся отдельным bounded VLM call.
    assert expected["Validate Project Context"] == 600_000


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
