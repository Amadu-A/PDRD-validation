# tests/architecture/test_experience_review_structure.py

"""Review-specific dependency and export-boundary architectural checks."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PACKAGE = ROOT / "services" / "experience-service" / "src" / "pdrd_experience_service"


def _imports(
    path: Path,
) -> set[str]:
    """Extract import names without matching incidental text or comments."""
    tree = ast.parse(
        path.read_text(
            encoding="utf-8",
        )
    )

    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(
            node,
            ast.Import,
        ):
            names.update(item.name for item in node.names)

        elif (
            isinstance(
                node,
                ast.ImportFrom,
            )
            and node.module
        ):
            names.add(
                node.module,
            )

    return names


def test_review_domain_and_application_preserve_dependency_direction() -> None:
    """Domain/application cannot depend on frameworks or infrastructure."""
    forbidden = (
        "sqlalchemy",
        "fastapi",
        "httpx",
        "fitz",
        "pydantic",
    )

    for folder in (
        PACKAGE / "domain",
        PACKAGE / "application",
    ):
        for source in folder.rglob("*.py"):
            imports = _imports(
                source,
            )

            assert all(not value.startswith(forbidden) for value in imports)

            assert all(".infrastructure" not in value for value in imports)

            assert all(".transport" not in value for value in imports)

            first = source.read_text(
                encoding="utf-8",
            ).splitlines()[0]

            assert first == (f"# {source.relative_to(ROOT).as_posix()}")


def test_review_has_explicit_cas_and_approved_snapshot_contract() -> None:
    """Prevent a browser-only decision from silently becoming a final PDF."""
    port = (PACKAGE / "application/ports/review.py").read_text(
        encoding="utf-8",
    )

    domain = (PACKAGE / "domain/review.py").read_text(
        encoding="utf-8",
    )

    assert "expected_revision: int" in port
    assert "def approve(" in domain
    assert "def accepted_for_pdf(" in domain
    assert "approved_revision != self.revision" in domain
    assert "self.pending_count" in domain
