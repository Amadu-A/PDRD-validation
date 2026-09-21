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


def _direct_functional_successor(
    workflow: dict[str, object],
    source_name: str,
) -> str:
    """Возвращает единственный прямой non-progress successor."""
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

    assert isinstance(
        main,
        list,
    )

    assert main

    branch = main[0]

    assert isinstance(
        branch,
        list,
    )

    assert branch

    successors = [
        str(
            connection["node"],
        )
        for connection in branch
        if (
            isinstance(
                connection,
                dict,
            )
            and "node" in connection
        )
    ]

    functional = [
        node_name
        for node_name in successors
        if not node_name.startswith(
            "Progress ",
        )
    ]

    assert (
        len(
            functional,
        )
        == 1
    ), (
        source_name,
        successors,
        functional,
    )

    return functional[0]


def _functional_successor(
    workflow: dict[str, object],
    source_name: str,
) -> str:
    """Возвращает business successor через infrastructure Gate nodes."""
    current_name = source_name

    visited: set[str] = set()

    for _ in range(
        32,
    ):
        successor = _direct_functional_successor(
            workflow,
            current_name,
        )

        if not successor.startswith(
            "Gate ",
        ):
            return successor

        assert successor not in visited, (
            source_name,
            successor,
        )

        visited.add(
            successor,
        )

        current_name = successor

    raise AssertionError(f"Превышена глубина infrastructure gate chain: {source_name}")


def _requirement_search_name(
    nodes: dict[
        str,
        dict[
            str,
            object,
        ],
    ],
) -> str:
    """Возвращает имя текущего requirement retrieval node."""
    if "Search Requirements" in nodes:
        return "Search Requirements"

    assert "Search Normative" in nodes

    return "Search Normative"


def _node_code(
    node: dict[str, object],
) -> str:
    """Возвращает jsCode Code node."""
    parameters = node["parameters"]

    assert isinstance(
        parameters,
        dict,
    )

    return str(
        parameters.get(
            "jsCode",
            "",
        )
    )


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

        body = str(
            parameters["body"],
        )

        assert "user_package_document_ids" in body

        assert "Build Normative Queries" in body


def test_n8n_keeps_requirement_and_package_sources_separate() -> None:
    """Check Norms получает typed N/T/U buckets без смешивания sources."""
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

        body = str(
            parameters["body"],
        )

        assert "normative_sources" in body

        assert "user_package_sources" in body

        if file_name == "analysis-v2-pdf.json":
            # Stage 8.4 сначала собирает page-local
            # N/T/U в один document-scoped GPU request.
            collector = nodes["Gate Collect Norm Check Stage"]

            collector_code = _node_code(
                collector,
            )

            assert "Normalize Requirement Search" in collector_code

            # Collector находится непосредственно после
            # Search User Packages progress/gate chain,
            # поэтому U-result приходит через $input.
            assert "const userPackages = $input.all()" in collector_code

            assert "normative_sources" in collector_code

            assert "technical_assignment_sources" in collector_code

            assert "conflict_candidates" in collector_code

            assert "user_package_sources" in collector_code

            assert (
                _functional_successor(
                    workflow,
                    "Search User Packages",
                )
                == "Check Norms"
            )

            continue

        if "Search Requirements" in nodes:
            assert "Normalize Requirement Search" in body

            assert "technical_assignment_sources" in body

            assert "conflict_candidates" in body

        else:
            assert "Search Normative" in body


def test_user_package_search_is_between_requirement_search_and_check() -> None:
    """Закрепляет requirement → U → check business flow."""
    for file_name in WORKFLOW_FILES:
        workflow = _load_workflow(
            file_name,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        search_name = _requirement_search_name(
            nodes,
        )

        if search_name == "Search Requirements":
            assert (
                _functional_successor(
                    workflow,
                    "Search Requirements",
                )
                == "Normalize Requirement Search"
            )

            assert (
                _functional_successor(
                    workflow,
                    "Normalize Requirement Search",
                )
                == "Search User Packages"
            )

        else:
            assert (
                _functional_successor(
                    workflow,
                    "Search Normative",
                )
                == "Search User Packages"
            )

        assert (
            _functional_successor(
                workflow,
                "Search User Packages",
            )
            == "Check Norms"
        )


def test_results_expose_package_sources_for_diagnostics() -> None:
    """Workflow result сохраняет U-sources отдельно от requirement sources."""
    for file_name in WORKFLOW_FILES:
        workflow_text = (WORKFLOW_ROOT / file_name).read_text(
            encoding="utf-8",
        )

        assert "user_package_sources" in workflow_text

        assert "user_package_search" in workflow_text
