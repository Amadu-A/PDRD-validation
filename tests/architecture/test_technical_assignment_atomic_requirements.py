# tests/architecture/test_technical_assignment_atomic_requirements.py

"""Architecture guards atomic T requirement representation."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)


def test_t_index_preserves_page_and_requirement_representations() -> None:
    """ТЗ хранит page representation и отдельные atomic requirements."""
    use_case = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "application"
        / "use_cases"
        / "index_technical_assignment.py"
    ).read_text(
        encoding="utf-8",
    )

    assert '"page_multimodal"' in use_case

    assert '"requirement_text"' in use_case

    assert "extract_technical_assignment_requirements" in use_case

    assert '"requirement_id"' in use_case

    assert '"parent_page_point_id"' in use_case


def test_existing_t_guided_retrieval_remains_page_scoped() -> None:
    """Этап 3 не включает T-first pass раньше Этапа 4."""
    retrieval = (
        ROOT
        / "services"
        / "knowledge-service"
        / "src"
        / "pdrd_knowledge_service"
        / "application"
        / "use_cases"
        / "technical_assignment_retrieval.py"
    ).read_text(
        encoding="utf-8",
    )

    assert '"page_multimodal"' in retrieval

    assert '"requirement_text"' not in retrieval
