# tests/architecture/test_analysis_visualization.py

"""Architecture guards hybrid end-to-end visualization wiring."""

import ast
import json
from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

FRONTEND_DIR = ROOT / "frontend" / "src" / "js" / "features" / "analysis"

VISUALIZATION_JS = FRONTEND_DIR / "visualization.js"

REPORT_JS = FRONTEND_DIR / "report.js"

CONTROLLER_JS = FRONTEND_DIR / "controller.js"

API_JS = FRONTEND_DIR / "api.js"

DOCUMENT_PYMUPDF = (
    ROOT
    / "services"
    / "document-service"
    / "src"
    / "pdrd_document_service"
    / "infrastructure"
    / "pdf"
    / "pymupdf.py"
)

DOCUMENT_PDF_SCHEMA = (
    ROOT
    / "services"
    / "document-service"
    / "src"
    / "pdrd_document_service"
    / "transport"
    / "http"
    / "schemas"
    / "pdf.py"
)

DOCUMENT_PDF_ROUTER = (
    ROOT
    / "services"
    / "document-service"
    / "src"
    / "pdrd_document_service"
    / "transport"
    / "http"
    / "routers"
    / "pdf.py"
)

GATEWAY_ROUTER = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "transport"
    / "http"
    / "routers"
    / "analyses.py"
)

GATEWAY_CONTAINER = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "core"
    / "container.py"
)

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

GATEWAY_ANCHOR_MATCHER = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "application"
    / "finding_anchor_matcher.py"
)

VISUALIZATION_RENDERER = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "infrastructure"
    / "visualization.py"
)

ANALYSIS_ROUTES = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "transport"
    / "http"
    / "routes.py"
)

ANALYSIS_CONTAINER = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "core"
    / "container.py"
)

LOCALIZATION_USE_CASE = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "application"
    / "use_cases"
    / "finding_localization.py"
)

WORKFLOW_DIR = ROOT / "n8n" / "workflows"


def _read(
    path: Path,
) -> str:
    """Читает text source."""
    return path.read_text(
        encoding="utf-8",
    )


def _workflow(
    file_name: str,
) -> dict[
    str,
    object,
]:
    """Читает committed n8n workflow."""
    return json.loads(
        (WORKFLOW_DIR / file_name).read_text(
            encoding="utf-8",
        )
    )


def test_document_service_preserves_pdf_text_geometry() -> None:
    """PyMuPDF word bbox должен доходить до optional HTTP contract."""
    pymupdf = _read(
        DOCUMENT_PYMUPDF,
    )

    schema = _read(
        DOCUMENT_PDF_SCHEMA,
    )

    router = _read(
        DOCUMENT_PDF_ROUTER,
    )

    assert '"words"' in pymupdf

    assert "PdfTextWord" in pymupdf

    assert "PdfNormalizedBoundingBox" in pymupdf

    assert "PdfTextWordResponse" in schema

    assert "text_words" in schema

    assert "include_text_geometry" in router

    assert "page.text_words" in router


def test_frontend_visualization_supports_multiple_regions() -> None:
    """Frontend рисует несколько bbox одного finding безопасно."""
    visualization = _read(
        VISUALIZATION_JS,
    )

    report = _read(
        REPORT_JS,
    )

    controller = _read(
        CONTROLLER_JS,
    )

    api = _read(
        API_JS,
    )

    required = (
        "page.locations",
        "location.regions",
        "region?.bbox",
        "x_min",
        "y_min",
        "x_max",
        "y_max",
        "createElementNS",
        "polyline",
        "analysis-result__bbox",
        "analysis-result__annotation",
        "analysis-result__annotation-tooltip",
        "source.text",
        "textContent",
    )

    missing = [marker for marker in required if marker not in visualization]

    assert not missing, "\n".join(
        missing,
    )

    assert "innerHTML" not in visualization

    assert 'from "./visualization.js"' in report

    assert "appendAnalysisVisualization(" in report

    assert "getAnalysisVisualization" in controller

    assert "getAnalysisVisualization" in api

    assert "/visualization" in api


def test_gateway_uses_pdf_geometry_before_vlm_fallback() -> None:
    """Exact PDF anchors должны иметь приоритет над VLM."""
    container = _read(
        GATEWAY_CONTAINER,
    )

    use_case = _read(
        GATEWAY_USE_CASE,
    )

    matcher = _read(
        GATEWAY_ANCHOR_MATCHER,
    )

    adapter = _read(
        VISUALIZATION_RENDERER,
    )

    router = _read(
        GATEWAY_ROUTER,
    )

    assert '"/{job_id}/visualization"' in router

    assert "FindingAnchorMatcher" in container

    assert "anchor_matcher=" in container

    assert "anchor_matcher.locate" in use_case

    assert "unresolved_targets" in use_case

    assert "finding_locator.localize" in use_case

    assert "pdf_text" in matcher

    assert "AnalysisVisualRegion" in matcher

    assert '"include_text_geometry"' in adapter

    assert '"text_words"' in adapter


def test_analysis_service_remains_vlm_fallback() -> None:
    """Analysis Service localization сохраняется для pure graphical cases."""
    routes = _read(
        ANALYSIS_ROUTES,
    )

    container = _read(
        ANALYSIS_CONTAINER,
    )

    localization = _read(
        LOCALIZATION_USE_CASE,
    )

    assert '"/internal/v1/findings/localize"' in routes

    assert "container.localize_findings.execute" in routes

    assert "localize_findings: LocalizeFindings" in container

    assert "def build_finding_localization_schema(" in localization

    assert "def build_finding_localization_prompt(" in localization


def test_pdf_renderer_uses_one_document_service_request() -> None:
    """Batch renderer не выполняет отдельный HTTP request на каждую page."""
    source = _read(
        VISUALIZATION_RENDERER,
    )

    tree = ast.parse(
        source,
    )

    pdf_extract_calls = [
        node
        for node in ast.walk(
            tree,
        )
        if (
            isinstance(
                node,
                ast.Constant,
            )
            and node.value == "/internal/v1/pdf/extract"
        )
    ]

    assert (
        len(
            pdf_extract_calls,
        )
        == 1
    )


def test_visual_localization_stays_out_of_core_n8n_workflows() -> None:
    """Visualization не удлиняет основной analysis workflow."""
    for file_name in (
        "analysis-v2-pdf.json",
        "analysis-v2-pdf-cad.json",
        "analysis-v2-cad.json",
    ):
        workflow = _workflow(
            file_name,
        )

        settings = workflow.get(
            "settings",
            {},
        )

        assert isinstance(
            settings,
            dict,
        )

        assert (
            settings.get(
                "executionTimeout",
            )
            == 1650
        )

        nodes = workflow.get(
            "nodes",
            [],
        )

        assert isinstance(
            nodes,
            list,
        )

        names = {
            str(
                node.get(
                    "name",
                    "",
                )
            )
            for node in nodes
            if isinstance(
                node,
                dict,
            )
        }

        assert "Localize Findings" not in names
