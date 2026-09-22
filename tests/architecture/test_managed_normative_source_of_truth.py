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

LEGACY_SOURCE_DIRECTORY = (
    REPOSITORY_ROOT / "data" / "knowledge" / "normative" / "source"
)

LEGACY_DIRECT_EMBEDDING_CLI_PATHS = (
    REPOSITORY_ROOT / "scripts" / "kb_common.py",
    REPOSITORY_ROOT / "scripts" / "kb_search.py",
    REPOSITORY_ROOT / "scripts" / "kb_sync.py",
    REPOSITORY_ROOT / "scripts" / "requirements-kb.txt",
)


def test_repository_has_no_legacy_normative_source_directory() -> None:
    """Нормативные originals больше не хранятся в Git."""
    assert not LEGACY_SOURCE_DIRECTORY.exists()


def test_repository_has_no_legacy_direct_embedding_cli() -> None:
    """Legacy Ollama/Qdrant CLI не должен обходить managed retrieval."""
    existing = [
        str(
            path.relative_to(
                REPOSITORY_ROOT,
            )
        )
        for path in LEGACY_DIRECT_EMBEDDING_CLI_PATHS
        if path.exists()
    ]

    assert not existing, "\n".join(
        existing,
    )
