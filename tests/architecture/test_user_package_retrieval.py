# tests/architecture/test_user_package_retrieval.py

"""Architecture guards separation normative and user-package retrieval."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

KNOWLEDGE_ROOT = (
    REPOSITORY_ROOT
    / "services"
    / "knowledge-service"
    / "src"
    / "pdrd_knowledge_service"
)

ANALYSIS_ROOT = (
    REPOSITORY_ROOT / "services" / "analysis-service" / "src" / "pdrd_analysis_service"
)


def test_user_package_search_has_separate_internal_endpoint() -> None:
    """Package retrieval не переиспользует normative HTTP route."""
    router = (
        KNOWLEDGE_ROOT / "transport" / "http" / "routers" / "search.py"
    ).read_text(
        encoding="utf-8",
    )

    assert '"/normative"' in router

    assert '"/user-packages"' in router

    assert "search_user_packages" in router


def test_package_search_requires_user_package_area() -> None:
    """Knowledge layer проверяет area до Qdrant retrieval."""
    use_case = (
        KNOWLEDGE_ROOT / "application" / "use_cases" / "user_packages.py"
    ).read_text(
        encoding="utf-8",
    )

    normative = (
        KNOWLEDGE_ROOT / "application" / "use_cases" / "normative.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "CatalogArea.USER_PACKAGE" in use_case

    assert "wrong_area_ids" in normative

    assert "expected_area" in normative


def test_qdrant_scope_uses_exact_managed_document_ids() -> None:
    """Qdrant scope строится из IDs после PostgreSQL validation."""
    normative = (
        KNOWLEDGE_ROOT / "application" / "use_cases" / "normative.py"
    ).read_text(
        encoding="utf-8",
    )

    assert 'key="document_id"' in normative

    assert "_build_scope_filter" in normative

    assert "_normalize_document_ids" in normative

    assert "list_by_ids" in normative


def test_analysis_keeps_user_sources_separate_from_normative_basis() -> None:
    """N- и U-sources имеют разные maps и разные evidence fields."""
    domain = (ANALYSIS_ROOT / "domain" / "analysis.py").read_text(
        encoding="utf-8",
    )

    schemas = (ANALYSIS_ROOT / "transport" / "http" / "schemas.py").read_text(
        encoding="utf-8",
    )

    prompt = (ANALYSIS_ROOT / "application" / "prompts.py").read_text(
        encoding="utf-8",
    )

    use_case = (ANALYSIS_ROOT / "application" / "use_cases" / "normative.py").read_text(
        encoding="utf-8",
    )

    assert "class UserPackageSource" in domain

    assert "user_package_source_ids" in domain

    assert "user_package_basis_sources" in domain

    assert "user_package_sources" in schemas

    assert "user_package_basis_sources" in schemas

    assert "USER PACKAGE SOURCES" in prompt

    assert "normative_source_ids" in prompt

    assert "user_package_source_ids" in prompt

    assert "source_by_id = {" in use_case

    assert "for source in normative_sources" in use_case

    assert "user_package_by_id = {" in use_case

    assert "for source in user_package_sources" in use_case

    basis_start = use_case.find(
        "basis=build_basis(",
    )

    basis_sources_start = use_case.find(
        "basis_sources=",
        basis_start,
    )

    assert basis_start >= 0

    assert basis_sources_start > basis_start

    basis_block = use_case[basis_start:basis_sources_start]

    assert "selected_normative_sources" in basis_block

    assert "selected_user_package_sources" not in basis_block

    assert "user_package_basis_sources=(" in use_case

    assert "selected_user_package_sources" in use_case
