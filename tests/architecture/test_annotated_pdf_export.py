# tests/architecture/test_annotated_pdf_export.py

"""Architecture guards lazy annotated PDF export."""

import json
from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

DOCUMENT_ANNOTATOR = (
    ROOT
    / "services"
    / "document-service"
    / "src"
    / "pdrd_document_service"
    / "infrastructure"
    / "pdf"
    / "annotator.py"
)

DOCUMENT_ROUTER = (
    ROOT
    / "services"
    / "document-service"
    / "src"
    / "pdrd_document_service"
    / "transport"
    / "http"
    / "routers"
    / "pdf_annotation.py"
)

GATEWAY_USE_CASE = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "application"
    / "use_cases"
    / "get_analysis_annotated_pdf.py"
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
    / "analysis_pdf_exports.py"
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

FRONTEND_EXPORT = (
    ROOT / "frontend" / "src" / "js" / "features" / "analysis" / "pdf-export.js"
)

FRONTEND_CONTROLLER = (
    ROOT / "frontend" / "src" / "js" / "features" / "analysis" / "controller.js"
)

FRONTEND_STYLE = ROOT / "frontend" / "src" / "css" / "style.css"

WORKFLOW_DIR = ROOT / "n8n" / "workflows"


def _read(
    path: Path,
) -> str:
    """Читает UTF-8 source."""
    return path.read_text(
        encoding="utf-8",
    )


def test_document_service_owns_pdf_annotation_mutation() -> None:
    """PyMuPDF mutation остаётся внутри Document Service."""
    annotator = _read(
        DOCUMENT_ANNOTATOR,
    )

    router = _read(
        DOCUMENT_ROUTER,
    )

    assert "add_rect_annot" in annotator

    assert "add_freetext_annot" in annotator

    assert "derotation_matrix" in annotator

    assert '"cjk"' in annotator

    assert "new_page" in annotator

    assert '"/annotate"' in router

    assert '"application/pdf"' in router


def test_gateway_exposes_lazy_download_and_keeps_typed_sources_separate() -> None:
    """Export endpoint использует N/T/U как раздельные buckets."""
    use_case = _read(
        GATEWAY_USE_CASE,
    )

    router = _read(
        GATEWAY_ROUTER,
    )

    container = _read(
        GATEWAY_CONTAINER,
    )

    assert '"/{job_id}/annotated-pdf"' in router

    assert "GetAnalysisAnnotatedPdf" in container

    assert "get_analysis_visualization" in use_case

    assert '"normative_sources"' in use_case

    assert '"technical_assignment_basis_sources"' in use_case

    assert '"user_package_basis_sources"' in use_case

    assert "Точное место автоматически" in use_case


def test_frontend_download_is_appended_after_report() -> None:
    """Download action находится в самом низу rendered result."""
    export = _read(
        FRONTEND_EXPORT,
    )

    controller = _read(
        FRONTEND_CONTROLLER,
    )

    style = _read(
        FRONTEND_STYLE,
    )

    assert "Скачать PDF с аннотациями" in export

    assert "/annotated-pdf" in export

    assert "appendAnnotatedPdfDownload" in controller

    report_position = controller.rfind(
        "renderAnalysisReport(",
    )

    export_position = controller.rfind(
        "appendAnnotatedPdfDownload(",
    )

    assert report_position >= 0

    assert export_position > report_position

    assert '@import url("./blocks/analysis-export.css");' in style


def test_annotated_pdf_export_stays_out_of_core_n8n() -> None:
    """PDF export остаётся lazy и не удлиняет analysis pipeline."""
    for file_name in (
        "analysis-v2-pdf.json",
        "analysis-v2-cad.json",
        "analysis-v2-pdf-cad.json",
    ):
        workflow = json.loads(
            (WORKFLOW_DIR / file_name).read_text(
                encoding="utf-8",
            )
        )

        serialized = json.dumps(
            workflow,
            ensure_ascii=False,
        )

        assert "annotated-pdf" not in serialized

        assert "/internal/v1/pdf/annotate" not in serialized
