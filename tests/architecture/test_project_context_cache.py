# tests/architecture/test_project_context_cache.py

"""Architecture guards reusable Project Context cache."""

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

PDF_WORKFLOWS = (
    (
        WORKFLOW_ROOT / "analysis-v2-pdf.json",
        "Document Extract PDF",
        "Expand PDF Pages",
        "Aggregate PDF Result",
        "Return PDF Result",
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        "Document Extract PDF CAD",
        "Prepare Combined Context",
        "Build PDF CAD Result",
        "Return PDF CAD Result",
    ),
)

ALL_WORKFLOWS = (
    WORKFLOW_ROOT / "analysis-v2-pdf.json",
    WORKFLOW_ROOT / "analysis-v2-cad.json",
    WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
)

GATEWAY_N8N_PATH = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "infrastructure"
    / "orchestration"
    / "n8n.py"
)


def _workflow(
    path: Path,
) -> dict[str, Any]:
    """Читает workflow JSON."""
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


def _nodes_by_name(
    workflow: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Индексирует workflow nodes по name."""
    nodes = workflow.get(
        "nodes",
        [],
    )

    assert isinstance(
        nodes,
        list,
    )

    return {
        str(
            node["name"],
        ): node
        for node in nodes
        if isinstance(
            node,
            dict,
        )
        and "name" in node
    }


def _next_node(
    workflow: dict[str, Any],
    source_name: str,
    *,
    branch: int = 0,
) -> str:
    """Возвращает первый main successor указанной ветки."""
    connections = workflow.get(
        "connections",
        {},
    )

    assert isinstance(
        connections,
        dict,
    )

    source = connections[source_name]

    main = source["main"]

    result = main[branch][0]["node"]

    assert isinstance(
        result,
        str,
    )

    return result


def _serialized_node(
    nodes: dict[str, dict[str, Any]],
    name: str,
) -> str:
    """Сериализует node для architecture assertions."""
    return json.dumps(
        nodes[name],
        ensure_ascii=False,
    )


def test_pdf_workflows_resolve_cache_before_vlm_validation() -> None:
    """PZ cache разрешается до potentially expensive VLM validation."""
    for (
        path,
        extract_name,
        _context_name,
        _result_name,
        _return_name,
    ) in PDF_WORKFLOWS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        assert "Resolve Project Context Cache" in nodes, path

        resolve = _serialized_node(
            nodes,
            "Resolve Project Context Cache",
        )

        assert "/internal/v1/project-contexts/resolve-cache" in resolve, path

        assert (
            _next_node(
                workflow,
                extract_name,
            )
            == "Resolve Project Context Cache"
        ), path

        assert (
            _next_node(
                workflow,
                "Resolve Project Context Cache",
            )
            == "Validate Project Context"
        ), path

        assert (
            _next_node(
                workflow,
                "Validate Project Context",
            )
            == "Create Project Context"
        ), path


def test_pdf_workflows_reuse_cached_validation_and_cache_identity() -> None:
    """Validate/Create stages получают reusable cache metadata."""
    for (
        path,
        _extract_name,
        context_name,
        _result_name,
        _return_name,
    ) in PDF_WORKFLOWS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        validate = _serialized_node(
            nodes,
            "Validate Project Context",
        )

        create = _serialized_node(
            nodes,
            "Create Project Context",
        )

        context = _serialized_node(
            nodes,
            context_name,
        )

        assert "cached_validation" in validate, path

        assert "Resolve Project Context Cache" in validate, path

        assert "cache_key" in create, path

        assert "validation:" in create, path

        assert "Resolve Project Context Cache" in create, path

        assert "cache_key" in context, path

        assert "cache_hit" in context, path

        assert "collection_name" in context, path


def test_pdf_workflows_do_not_delete_persistent_cache_after_job() -> None:
    """Reusable Project Context остаётся после завершения analysis job."""
    for (
        path,
        _extract_name,
        _context_name,
        result_name,
        return_name,
    ) in PDF_WORKFLOWS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        assert "Cleanup Project Context" not in nodes, path

        assert (
            _next_node(
                workflow,
                result_name,
            )
            == return_name
        ), path

        serialized = json.dumps(
            workflow,
            ensure_ascii=False,
        )

        assert "temporary_project_context_index" not in serialized, path

        assert "project_context_cleanup" not in serialized, path

        assert "project_context_cache" in serialized, path


def test_all_workflows_use_prepared_technical_assignment_document_identity() -> None:
    """T-first сверяет requirements с prepared identity исходного ТЗ."""
    for path in ALL_WORKFLOWS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        feed = _serialized_node(
            nodes,
            "Technical Assignment Requirement Feed",
        )

        assert "technical_assignment_analysis_document_id" in feed, path

        assert "body?.document_id ?? ''" not in feed, path


def test_api_gateway_forwards_prepared_technical_assignment_identity() -> None:
    """Gateway передаёт n8n обе T identities из immutable snapshot."""
    source = GATEWAY_N8N_PATH.read_text(
        encoding="utf-8",
    )

    assert "technical_assignment_analysis_document_id" in source

    assert "technical_assignment.analysis_document_id" in source
