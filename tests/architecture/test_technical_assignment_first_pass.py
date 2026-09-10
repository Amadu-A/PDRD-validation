# tests/architecture/test_technical_assignment_first_pass.py

"""Architecture guards independent T-first validation."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)


def test_t_first_validation_does_not_depend_on_retrieval() -> None:
    """Atomic T validation не управляется similarity search."""
    path = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "application"
        / "use_cases"
        / "technical_assignment_validation.py"
    )

    source = path.read_text(
        encoding="utf-8",
    )

    assert "TechnicalAssignmentRequirement" in source

    assert "embedding" not in source.lower()

    assert "vector_store" not in source

    assert "SearchTechnicalAssignment" not in source

    assert "confidence_threshold" not in source

    assert "min_confidence" not in source

    assert "if decision.confidence" not in source


def test_t_first_validation_keeps_uncertain_candidates() -> None:
    """insufficient_evidence становится finding, а не отбрасывается."""
    path = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "application"
        / "use_cases"
        / "technical_assignment_validation.py"
    )

    source = path.read_text(
        encoding="utf-8",
    )

    assert '"violated"' in source

    assert '"insufficient_evidence"' in source

    assert '"needs_review"' in source

    assert '"customer_requirements"' in source


def test_t_first_validation_has_dedicated_http_route() -> None:
    """Independent validator доступен отдельным internal API."""
    path = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "transport"
        / "http"
        / "technical_assignment_routes.py"
    )

    source = path.read_text(
        encoding="utf-8",
    )

    assert "/internal/v1/pages/check-technical-assignment" in source

    assert "check_page_against_technical_assignment" in source


def test_t_first_batch_policy_is_configurable() -> None:
    """Batching T requirements хранится в baseline configuration."""
    settings_path = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "core"
        / "settings.py"
    )

    settings_source = settings_path.read_text(
        encoding="utf-8",
    )

    environment_source = (ROOT / ".env.example").read_text(
        encoding="utf-8",
    )

    assert "technical_assignment_batch_size" in settings_source

    assert "technical_assignment_requirement_text_limit" in settings_source

    assert (
        "ANALYSIS_SERVICE_PIPELINE__"
        "TECHNICAL_ASSIGNMENT_BATCH_SIZE=20" in environment_source
    )

    assert (
        "ANALYSIS_SERVICE_PIPELINE__"
        "TECHNICAL_ASSIGNMENT_REQUIREMENT_TEXT_LIMIT=1800" in environment_source
    )
