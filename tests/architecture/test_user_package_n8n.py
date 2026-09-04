# tests/architecture/test_user_package_n8n.py

"""Architecture guards n8n user-package retrieval."""

import json
from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

WORKFLOW_ROOT = REPOSITORY_ROOT / "n8n" / "workflows"

WORKFLOW_FILES = (
    "analysis-v2-pdf.json",
    "analysis-v2-cad.json",
    "analysis-v2-pdf-cad.json",
)


def _load_workflow(
    file_name: str,
) -> dict[str, object]:
    """Читает workflow JSON."""
    return json.loads(
        (WORKFLOW_ROOT / file_name).read_text(
            encoding="utf-8",
        )
    )


def _nodes_by_name(
    workflow: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Индексирует workflow nodes по name."""
    nodes = workflow["nodes"]

    assert isinstance(
        nodes,
        list,
    )

    return {
        node["name"]: node
        for node in nodes
        if isinstance(
            node,
            dict,
        )
    }


def _next_node(
    workflow: dict[str, object],
    source_name: str,
) -> str:
    """Возвращает единственный main successor."""
    connections = workflow["connections"]

    assert isinstance(
        connections,
        dict,
    )

    source = connections[source_name]

    assert isinstance(
        source,
        dict,
    )

    main = source["main"]

    return main[0][0]["node"]


def test_all_v2_workflows_search_user_packages() -> None:
    """Все analysis workflows содержат отдельный U-source retrieval."""
    for file_name in WORKFLOW_FILES:
        workflow = _load_workflow(
            file_name,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        assert "Search User Packages" in nodes

        node = nodes["Search User Packages"]

        parameters = node["parameters"]

        assert isinstance(
            parameters,
            dict,
        )

        assert parameters["url"] == (
            "http://pdrd-knowledge-service:8401/internal/v1/search/user-packages"
        )

        body = str(parameters["body"])

        assert "user_package_document_ids" in body

        assert "Build Normative Queries" in body


def test_n8n_keeps_normative_and_package_sources_separate() -> None:
    """Check Norms получает N и U sources разными полями."""
    for file_name in WORKFLOW_FILES:
        workflow = _load_workflow(
            file_name,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        check = nodes["Check Norms"]

        parameters = check["parameters"]

        assert isinstance(
            parameters,
            dict,
        )

        body = str(parameters["body"])

        assert "normative_sources" in body

        assert "user_package_sources" in body

        assert "Search Normative" in body


def test_user_package_search_is_between_normative_search_and_check() -> None:
    """Закрепляет deterministic retrieval chain."""
    for file_name in WORKFLOW_FILES:
        workflow = _load_workflow(
            file_name,
        )

        assert (
            _next_node(
                workflow,
                "Search Normative",
            )
            == "Search User Packages"
        )

        assert (
            _next_node(
                workflow,
                "Search User Packages",
            )
            == "Check Norms"
        )


def test_results_expose_package_sources_for_diagnostics() -> None:
    """Workflow result сохраняет U-sources отдельно от normative sources."""
    for file_name in WORKFLOW_FILES:
        workflow_text = (WORKFLOW_ROOT / file_name).read_text(
            encoding="utf-8",
        )

        assert "user_package_sources" in workflow_text

        assert "user_package_search" in workflow_text
