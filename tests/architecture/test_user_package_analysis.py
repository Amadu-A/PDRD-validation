# tests/architecture/test_user_package_analysis.py

"""Architecture guards user-package selection и analysis snapshot."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

FRONTEND = REPOSITORY_ROOT / "frontend" / "src"

API_JS = FRONTEND / "js" / "features" / "normative" / "api.js"

PACKAGES_JS = FRONTEND / "js" / "features" / "normative" / "user_packages.js"

APP_JS = FRONTEND / "js" / "app.js"

FORM_JS = FRONTEND / "js" / "features" / "analysis" / "form.js"

SNAPSHOT = (
    REPOSITORY_ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "domain"
    / "normative_snapshot.py"
)

ORCHESTRATOR = (
    REPOSITORY_ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "infrastructure"
    / "orchestration"
    / "n8n.py"
)


def test_frontend_uses_separate_user_package_api() -> None:
    """Package CRUD не использует normative document routes."""
    content = API_JS.read_text(
        encoding="utf-8",
    )

    required = (
        "listUserPackageCategories",
        "createUserPackageCategory",
        "listUserPackageDocuments",
        "uploadUserPackageDocument",
        "queueUserPackageDocument",
        "moveUserPackageDocument",
        "deleteUserPackageDocument",
        "userPackageDocumentContentUrl",
        "/user-packages/categories",
        "/user-packages/documents",
    )

    missing = [marker for marker in required if marker not in content]

    assert not missing, "\n".join(
        missing,
    )


def test_package_checkboxes_are_visible_and_selectable() -> None:
    """Package selection остаётся пользовательской, а не скрытой."""
    content = PACKAGES_JS.read_text(
        encoding="utf-8",
    )

    assert '"normative-sidebar__checkbox"' in content

    assert '"normative-sidebar__checkbox is-hidden"' not in content

    assert "selectAllButton" in content

    assert "clearSelectionButton" in content

    assert "getSelection" in content


def test_app_merges_package_selection_into_analysis_form() -> None:
    """Selection package controller относится к тому же section."""
    content = APP_JS.read_text(
        encoding="utf-8",
    )

    required = (
        "createUserPackageCatalog",
        "userPackageCatalog.setSection(",
        "userPackageCatalog.getSelection()",
        "userPackageDocumentIds",
        "packageSelection.sectionId",
        "selection.sectionId",
    )

    missing = [marker for marker in required if marker not in content]

    assert not missing, "\n".join(
        missing,
    )


def test_analysis_form_serializes_package_ids_separately() -> None:
    """Package IDs не смешиваются с нормативным document_ids."""
    content = FORM_JS.read_text(
        encoding="utf-8",
    )

    assert '"normative_document_ids"' in content

    assert '"user_package_document_ids"' in content

    assert "selection.userPackageDocumentIds" in content


def test_snapshot_and_n8n_keep_package_ids_separate() -> None:
    """Immutable snapshot и orchestration имеют отдельное package field."""
    snapshot = SNAPSHOT.read_text(
        encoding="utf-8",
    )

    orchestrator = ORCHESTRATOR.read_text(
        encoding="utf-8",
    )

    assert "user_package_document_ids" in snapshot

    assert '"user_package_document_ids"' in orchestrator

    assert "snapshot.user_package_document_ids" in orchestrator
