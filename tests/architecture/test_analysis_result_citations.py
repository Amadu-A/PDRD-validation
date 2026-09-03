# tests/architecture/test_analysis_result_citations.py

"""Architecture guards структурированного результата и citations."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

FRONTEND_SOURCE = REPOSITORY_ROOT / "frontend" / "src"

INDEX_HTML = FRONTEND_SOURCE / "index.html"

REPORT_JS = FRONTEND_SOURCE / "js" / "features" / "analysis" / "report.js"

RESULT_JS = FRONTEND_SOURCE / "js" / "components" / "result.js"

CONTROLLER_JS = FRONTEND_SOURCE / "js" / "features" / "analysis" / "controller.js"

ANALYSIS_DOMAIN = (
    REPOSITORY_ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "domain"
    / "analysis.py"
)

FINALIZATION_USE_CASE = (
    REPOSITORY_ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "application"
    / "use_cases"
    / "finalization.py"
)

NORMATIVE_ROUTER = (
    REPOSITORY_ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "transport"
    / "http"
    / "routers"
    / "normative_catalog.py"
)


def test_analysis_result_is_structured_container() -> None:
    """Финальный result больше не должен быть одним pre."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    assert 'class="analysis-result"' in html

    assert "<pre" not in html

    assert "analysis-result__placeholder" in html

    controller = CONTROLLER_JS.read_text(
        encoding="utf-8",
    )

    result = RESULT_JS.read_text(
        encoding="utf-8",
    )

    assert "resultView.showReport(" in controller

    assert "replaceChildren(" in result


def test_normative_citation_opens_managed_pdf_page_safely() -> None:
    """Citation использует document_id + physical page через Gateway."""
    report = REPORT_JS.read_text(
        encoding="utf-8",
    )

    required = (
        "source.document_id",
        "source.page",
        "/api/v1/normative/documents/",
        "/content#page=",
        "encodeURIComponent",
        'link.target = "_blank"',
        '"noopener noreferrer"',
        "dataset.normativeCitation",
        "dataset.documentId",
        "dataset.page",
        "textContent",
    )

    missing = [marker for marker in required if marker not in report]

    assert not missing, "\n".join(
        missing,
    )

    assert "innerHTML" not in report

    assert "innerHTML" not in RESULT_JS.read_text(
        encoding="utf-8",
    )


def test_backend_preserves_managed_citation_metadata() -> None:
    """Backend contract сохраняет document_id/page до final finding."""
    domain = ANALYSIS_DOMAIN.read_text(
        encoding="utf-8",
    )

    finalization = FINALIZATION_USE_CASE.read_text(
        encoding="utf-8",
    )

    router = NORMATIVE_ROUTER.read_text(
        encoding="utf-8",
    )

    assert "document_id: str | None" in domain

    assert "page: int | str | None" in domain

    assert "basis_sources=(finding.basis_sources)" in finalization

    assert '"/documents/{document_id}/content"' in router

    assert "Content-Disposition" in router

    assert "inline;" in router
