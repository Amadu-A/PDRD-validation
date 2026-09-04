# tests/architecture/test_readme_diagrams.py

"""Architecture guards GitHub-renderable README diagrams."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

README = REPOSITORY_ROOT / "README.md"


def test_physical_storage_mermaid_quotes_paths() -> None:
    """Filesystem paths не должны интерпретироваться как Mermaid shapes."""
    content = README.read_text(
        encoding="utf-8",
    )

    required = (
        'PGV --> PGP["/var/lib/postgresql/data"]',
        'QDV --> QDP["/qdrant/storage"]',
        'AV --> AP["/data/analyses"]',
        'NV --> NP["/data/normative"]',
    )

    missing = [marker for marker in required if marker not in content]

    assert not missing, "\n".join(
        missing,
    )

    assert "PGP[/var/lib/postgresql/data]" not in content

    assert "QDP[/qdrant/storage]" not in content

    assert "AP[/data/analyses]" not in content

    assert "NP[/data/normative]" not in content


def test_readme_documents_separate_n_and_u_sources() -> None:
    """README закрепляет semantic boundary нормативов и packages."""
    content = README.read_text(
        encoding="utf-8",
    )

    required = (
        "user_package_document_ids",
        "N1, N2, N3",
        "U1, U2, U3",
        "normative_source_ids",
        "catalog_area=user_package",
        "Search User Packages",
    )

    missing = [marker for marker in required if marker not in content]

    assert not missing, "\n".join(
        missing,
    )
