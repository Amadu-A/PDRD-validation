# tests/architecture/test_user_package_result_citations.py

"""Architecture guards clickable user-package citations."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

REPORT_JS = (
    REPOSITORY_ROOT / "frontend" / "src" / "js" / "features" / "analysis" / "report.js"
)

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

USER_PACKAGE_ROUTER = (
    REPOSITORY_ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "transport"
    / "http"
    / "routers"
    / "user_packages.py"
)


def test_frontend_builds_clickable_user_package_citation() -> None:
    """U-source открывается через отдельный package content route."""
    report = REPORT_JS.read_text(
        encoding="utf-8",
    )

    required = (
        "user_package_basis_sources",
        "user-packages/documents/",
        "dataset.userPackageCitation",
        "Пользовательские требования / документы",
        "encodeURIComponent",
        "/content#page=",
        'link.target = "_blank"',
        '"noopener noreferrer"',
    )

    missing = [marker for marker in required if marker not in report]

    assert not missing, "\n".join(
        missing,
    )

    assert "innerHTML" not in report


def test_backend_preserves_user_package_basis_to_final_finding() -> None:
    """Analysis domain и finalization не теряют U-source metadata."""
    domain = ANALYSIS_DOMAIN.read_text(
        encoding="utf-8",
    )

    finalization = FINALIZATION_USE_CASE.read_text(
        encoding="utf-8",
    )

    assert "user_package_source_ids" in domain

    assert "user_package_basis_sources" in domain

    assert "finding.user_package_basis_sources" in finalization


def test_gateway_exposes_user_package_content_inline() -> None:
    """Публичный Gateway route умеет открыть package PDF/preview."""
    router = USER_PACKAGE_ROUTER.read_text(
        encoding="utf-8",
    )

    assert '"/user-packages/documents/{document_id}/content"' in router

    assert "Content-Disposition" in router

    assert "inline;" in router
