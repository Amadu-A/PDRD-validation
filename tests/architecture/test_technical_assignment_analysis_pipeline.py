# tests/architecture/test_technical_assignment_analysis_pipeline.py

"""Architecture guards TZ-5 T evidence pipeline."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)


def test_analysis_domain_keeps_t_separate_from_n_and_u() -> None:
    """Finding contract содержит отдельный T provenance."""
    domain = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "domain"
        / "analysis.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "class TechnicalAssignmentSource" in domain

    assert "technical_assignment_source_ids" in domain

    assert "technical_assignment_basis_sources" in domain

    assert "user_package_basis_sources" in domain

    assert "basis_sources" in domain


def test_analysis_prompt_declares_n_t_u_semantics() -> None:
    """Super-system prompt запрещает превращать T в норматив."""
    prompt = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "application"
        / "prompts.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "TECHNICAL ASSIGNMENT SOURCES" in prompt

    assert "T-source сам по себе НЕ доказывает" in prompt

    assert "T-source не может отменять" in prompt

    assert "CONFLICT CANDIDATES" in prompt


def test_gateway_sends_t_id_to_n8n() -> None:
    """n8n получает immutable technical_assignment_id."""
    adapter = (
        ROOT
        / "services"
        / "api-gateway"
        / "src"
        / "pdrd_api_gateway"
        / "infrastructure"
        / "orchestration"
        / "n8n.py"
    ).read_text(
        encoding="utf-8",
    )

    assert 'data["technical_assignment_id"]' in adapter

    assert "snapshot.technical_assignment" in adapter


def test_public_t_content_route_exists() -> None:
    """Frontend citation проходит только через Gateway."""
    main = (
        ROOT / "services" / "api-gateway" / "src" / "pdrd_api_gateway" / "main.py"
    ).read_text(
        encoding="utf-8",
    )

    router = (
        ROOT
        / "services"
        / "api-gateway"
        / "src"
        / "pdrd_api_gateway"
        / "transport"
        / "http"
        / "routers"
        / "technical_assignments.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "technical_assignments_router" in main

    assert "/api/v1/normative/technical-assignments" in router

    assert "/{technical_assignment_id}/content" in router


def test_frontend_renders_clickable_t_citation() -> None:
    """Report содержит отдельный clickable T-source."""
    report = (
        ROOT / "frontend" / "src" / "js" / "features" / "analysis" / "report.js"
    ).read_text(
        encoding="utf-8",
    )

    assert "technical_assignment_basis_sources" in report

    assert "technicalAssignmentCitationUrl" in report

    assert "/api/v1/normative/technical-assignments/" in report

    assert "technicalAssignmentCitation" in report
