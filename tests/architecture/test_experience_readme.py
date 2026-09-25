# tests/architecture/test_experience_readme.py

"""README architecture guards for current versus planned Experience capabilities."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

README = ROOT / "README.md"


def test_readme_documents_all_review_process_diagrams() -> None:
    """Prevent a code-only Experience roadmap without grouping/selection diagrams."""
    text = README.read_text(
        encoding="utf-8",
    )

    required = (
        "## 10. Группировка и локализация замечаний",
        "## 11. Полный бизнес-процесс Experience Service",
        "## 12. Подтверждение и исправление областей",
        "## 13. Как будет происходить отбор, отсечение и классификация",
        "## 14. Экспорт Reviewed PDF и обучение",
        "needs_adjudication",
        "ConfirmedFindingArea",
        "approved_revision",
        "experience.review_events",
        "KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=false",
    )

    missing = [item for item in required if item not in text]

    assert not missing, missing


def test_readme_does_not_claim_unreleased_e_search_or_reviewed_pdf() -> None:
    """Planned indexing/export cannot be mistaken for production functionality."""
    text = README.read_text(
        encoding="utf-8",
    )

    assert "публичного Experience API ещё нет" in text

    assert "reviewed PDF после Human Review пока заблокирован" in text

    assert "shared-vlm:8000/v1" in text

    assert "shared-embedding:8000/v1" in text

    assert "PDRD_EMBEDDING_SCHEMA_VERSION=2" in text
