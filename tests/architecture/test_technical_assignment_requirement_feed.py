# tests/architecture/test_technical_assignment_requirement_feed.py

"""Architecture guards independent T-first candidate feed."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)


def test_requirement_feed_reads_atomic_points_without_embeddings() -> None:
    """T-first feed не зависит от page-driven embedding queries."""
    use_case = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "application"
        / "use_cases"
        / "technical_assignment_requirements.py"
    ).read_text(
        encoding="utf-8",
    )

    assert "list_requirements" in use_case

    assert "embedding_provider" not in use_case

    assert "SearchTechnicalAssignment" not in use_case


def test_requirement_reader_is_strictly_scoped() -> None:
    """Qdrant reader фильтрует immutable T scope и representation."""
    reader = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "infrastructure"
        / "vector_store"
        / "technical_assignment_requirements.py"
    ).read_text(
        encoding="utf-8",
    )

    assert '"technical_assignment_id"' in reader

    assert '"analysis_document_id"' in reader

    assert '"section_id"' in reader

    assert '"requirement_text"' in reader

    assert '"with_vector": False' in reader


def test_requirement_feed_has_dedicated_internal_route() -> None:
    """n8n получает atomic T requirements через bounded API."""
    router = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "transport"
        / "http"
        / "routers"
        / "technical_assignments.py"
    ).read_text(
        encoding="utf-8",
    )

    assert '"/{technical_assignment_id}/requirements"' in router

    assert "response_model=TechnicalAssignmentRequirementListResponse" in router
