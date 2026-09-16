# tests/architecture/test_visualization_artifact_cache.py

"""Architecture guards Stage 7 reusable visualization artifact."""

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
    ),
    (
        WORKFLOW_ROOT / "analysis-v2-pdf-cad.json",
        "Document Extract PDF CAD",
    ),
)

CAD_ONLY_WORKFLOW = WORKFLOW_ROOT / "analysis-v2-cad.json"

GATEWAY_USE_CASE = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "application"
    / "use_cases"
    / "get_analysis_visualization.py"
)

GATEWAY_ARTIFACT_PORT = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "application"
    / "ports"
    / "artifacts.py"
)

GATEWAY_ARTIFACT_STORAGE = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "infrastructure"
    / "storage"
    / "filesystem.py"
)

GATEWAY_INTERNAL_ROUTER = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "transport"
    / "http"
    / "routers"
    / "analysis_artifacts.py"
)

GATEWAY_MAIN = (
    ROOT / "services" / "api-gateway" / "src" / "pdrd_api_gateway" / "main.py"
)

DOCUMENT_COMBINED_ROUTER = (
    ROOT
    / "services"
    / "document-service"
    / "src"
    / "pdrd_document_service"
    / "transport"
    / "http"
    / "routers"
    / "combined.py"
)


def _workflow(
    path: Path,
) -> dict[str, Any]:
    """Читает committed n8n workflow."""
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
) -> str:
    """Возвращает первый main successor node."""
    connections = workflow.get(
        "connections",
        {},
    )

    assert isinstance(
        connections,
        dict,
    )

    source = connections[source_name]

    return str(
        source["main"][0][0]["node"],
    )


def test_pdf_workflows_persist_initial_visualization_artifact() -> None:
    """Initial extraction сохраняется до дальнейшего analysis pipeline."""
    for (
        path,
        extract_name,
    ) in PDF_WORKFLOWS:
        workflow = _workflow(
            path,
        )

        nodes = _nodes_by_name(
            workflow,
        )

        assert "Persist Visualization Artifact" in nodes, path

        extract = json.dumps(
            nodes[extract_name],
            ensure_ascii=False,
        )

        persist_node = nodes["Persist Visualization Artifact"]

        persist = json.dumps(
            persist_node,
            ensure_ascii=False,
        )

        assert "include_text_geometry" in extract, path

        assert "/internal/v1/analysis-artifacts/" in persist, path

        assert "/visualization" in persist, path

        assert "image_base64" in persist, path

        assert "text_words" in persist, path

        assert "extracted_text" in persist, path

        assert (
            persist_node.get(
                "onError",
            )
            == "continueRegularOutput"
            or persist_node.get(
                "continueOnFail",
            )
            is True
        ), path

        assert (
            _next_node(
                workflow,
                extract_name,
            )
            == "Persist Visualization Artifact"
        ), path

        assert (
            _next_node(
                workflow,
                "Persist Visualization Artifact",
            )
            == "Resolve Project Context Cache"
        ), path


def test_cad_only_workflow_does_not_create_pdf_visualization_artifact() -> None:
    """CAD-only остаётся без фиктивного PDF artifact."""
    workflow = _workflow(
        CAD_ONLY_WORKFLOW,
    )

    nodes = _nodes_by_name(
        workflow,
    )

    assert "Persist Visualization Artifact" not in nodes


def test_gateway_reads_artifact_before_legacy_pdf_rerender() -> None:
    """Новый job не должен доходить до Document Service renderer."""
    source = GATEWAY_USE_CASE.read_text(
        encoding="utf-8",
    )

    load_position = source.find(
        "load_visualization(",
    )

    render_position = source.find(
        "pdf_page_renderer.render(",
    )

    assert load_position >= 0

    assert render_position >= 0

    assert load_position < render_position

    assert "AnalysisArtifactStorageError" in source

    assert "fallback_persisted" in source


def test_visualization_artifact_is_separate_from_analysis_result_json() -> None:
    """PNG сохраняется binary files, а не base64 внутри result.json."""
    port = GATEWAY_ARTIFACT_PORT.read_text(
        encoding="utf-8",
    )

    storage = GATEWAY_ARTIFACT_STORAGE.read_text(
        encoding="utf-8",
    )

    assert "save_visualization" in port

    assert "load_visualization" in port

    assert '_VISUALIZATION_DIRECTORY = "visualization"' in storage

    assert '"image_file"' in storage

    assert 'f"page-{page.page_number}.png"' in storage

    assert "_decode_png" in storage


def test_internal_artifact_route_is_registered() -> None:
    """n8n имеет отдельный internal ingress для initial extraction artifact."""
    router = GATEWAY_INTERNAL_ROUTER.read_text(
        encoding="utf-8",
    )

    main = GATEWAY_MAIN.read_text(
        encoding="utf-8",
    )

    assert 'prefix="/internal/v1/analysis-artifacts"' in router

    assert '"/{document_id}/visualization"' in router

    assert "save_visualization(" in router

    assert "analysis_artifacts_router" in main

    assert "include_router(\n        analysis_artifacts_router" in main


def test_combined_extract_can_return_pdf_text_geometry() -> None:
    """PDF+CAD initial extraction предоставляет ту же PDF geometry."""
    source = DOCUMENT_COMBINED_ROUTER.read_text(
        encoding="utf-8",
    )

    assert "include_text_geometry" in source

    assert "PdfTextWordResponse" in source

    assert "PdfNormalizedBoundingBoxResponse" in source

    assert "page.text_words" in source
