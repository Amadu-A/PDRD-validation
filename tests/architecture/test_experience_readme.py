# tests/architecture/test_experience_readme.py

"""Проверки README: разделение реализованного Review и будущего каталога Experience."""

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
    """Закрытый Review не выдаётся за готовый каталог, поиск E или Reviewed PDF."""
    text = README.read_text(
        encoding="utf-8",
    )

    assert "постоянный каталог Experience и Reviewed PDF ещё не реализованы" in text
    assert "Закрытый Review API доступен через Gateway" in text

    assert "reviewed PDF после Human Review пока заблокирован" in text

    assert "shared-vlm:8000/v1" in text

    assert "shared-embedding:8000/v1" in text

    assert "PDRD_EMBEDDING_SCHEMA_VERSION=2" in text
