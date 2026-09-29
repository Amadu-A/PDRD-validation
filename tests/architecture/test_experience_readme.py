# tests/architecture/test_experience_readme.py

"""README отражает работающий Review/каталог и отдельно будущий поиск Experience."""

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
    """Сохраняет схемы группировки, проверки областей и отдельного отбора Experience."""
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


def test_readme_documents_catalog_without_enabling_experience_search() -> None:
    """Каталог и экспорт реализованы, но не объявляют индексацию или обучение."""
    text = README.read_text(
        encoding="utf-8",
    )

    assert (
        "Постоянный каталог Experience, crop, CRUD и экспорт примеров реализованы"
        in text
    )
    assert "docs/experience-catalog.md" in text
    assert "negative_target=original|revised|both" in text
    assert "Закрытый Review API доступен через Gateway" in text
    assert "POST /api/v1/analyses/{job_id}/reviewed-pdf" in text
    assert "Reviewed PDF доступен только для текущей утверждённой редакции" in text
    assert "подтверждённой области включаются только в полный текстовый список" in text
    assert "confirmation digest + renderer version" in text
    assert "needs_adjudication" in text
    assert "KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=false" in text

    assert "shared-vlm:8000/v1" in text

    assert "shared-embedding:8000/v1" in text

    assert "PDRD_EMBEDDING_SCHEMA_VERSION=2" in text
