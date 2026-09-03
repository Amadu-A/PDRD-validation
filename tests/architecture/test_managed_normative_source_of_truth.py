# tests/architecture/test_managed_normative_source_of_truth.py

"""Architecture guards managed normative source of truth."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

KB_SYNC = REPOSITORY_ROOT / "scripts" / "kb_sync.py"

KB_SEARCH = REPOSITORY_ROOT / "scripts" / "kb_search.py"

LEGACY_SOURCE_DIRECTORY = (
    REPOSITORY_ROOT / "data" / "knowledge" / "normative" / "source"
)


def test_repository_has_no_legacy_normative_source_directory() -> None:
    """Нормативные originals больше не хранятся в Git."""
    assert not LEGACY_SOURCE_DIRECTORY.exists()


def test_kb_sync_cannot_index_normative_collection() -> None:
    """Legacy sync не должен писать managed нормативы напрямую."""
    content = KB_SYNC.read_text(
        encoding="utf-8",
    )

    forbidden = (
        "prepare_normative_chunks",
        "normative_dir",
        "settings.normative_collection",
    )

    violations = [marker for marker in forbidden if marker in content]

    assert not violations, "\n".join(
        violations,
    )

    assert "settings.experience_collection" in content


def test_kb_search_cannot_bypass_managed_normative_scope() -> None:
    """Legacy diagnostic search не должен читать normative unscoped."""
    content = KB_SEARCH.read_text(
        encoding="utf-8",
    )

    assert "settings.normative_collection" not in content

    assert "print_normative_results" not in content

    assert "settings.experience_collection" in content
