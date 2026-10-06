# tests/architecture/test_user_package_analysis.py

"""Проверяет архитектурные границы выбора пакетов и снимка анализа."""

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
    """Операции личных пакетов не используют нормативные маршруты документов."""
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
    """Приложение передаёт непустой выбор личных пакетов; сервер проверяет владельца."""
    content = APP_JS.read_text(
        encoding="utf-8",
    )
    access = (APP_JS.parent / "features" / "auth" / "main-access.js").read_text(
        encoding="utf-8"
    )

    assert "userPackageDocumentIds: []" not in content
    assert "createUserPackageCatalog" in content
    assert "userPackageCatalog.getSelection()?.documentIds.length" in content
    assert "inert = !canUsePackages" in access
    assert '"user_documents.own.read"' in access


def test_analysis_form_serializes_package_ids_separately() -> None:
    """Package IDs не смешиваются с нормативным document_ids."""
    content = FORM_JS.read_text(
        encoding="utf-8",
    )

    assert '"normative_document_ids"' in content

    assert '"user_package_document_ids"' in content

    assert "selection.userPackageDocumentIds" in content


def test_snapshot_and_n8n_keep_package_ids_separate() -> None:
    """Снимок и оркестрация сохраняют отдельное поле личных пакетов."""
    snapshot = SNAPSHOT.read_text(
        encoding="utf-8",
    )

    orchestrator = ORCHESTRATOR.read_text(
        encoding="utf-8",
    )

    assert "user_package_document_ids" in snapshot

    assert '"user_package_document_ids"' in orchestrator

    assert "snapshot.user_package_document_ids" in orchestrator


def test_package_owner_and_section_grants_stay_in_their_services() -> None:
    """Knowledge хранит владельца; User хранит гранты без FK в чужую схему."""
    import ast

    services = REPOSITORY_ROOT / "services"
    user_models = (
        services
        / "user-service/src/pdrd_user_service/infrastructure/database/models.py"
    )
    knowledge_models = (
        services
        / "knowledge-service/src/pdrd_knowledge_service/infrastructure/database/models.py"
    )
    for path, classes, field in (
        (
            knowledge_models,
            {"NormativeCategoryModel", "NormativeDocumentModel"},
            "owner_user_id",
        ),
        (user_models, {"UserSectionModel"}, "section_id"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        nodes = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name in classes
        ]
        assert len(nodes) == len(classes)
        for node in nodes:
            declarations = [
                item
                for item in node.body
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and item.target.id == field
            ]
            assert len(declarations) == 1
            assert "ForeignKey" not in ast.unparse(declarations[0])
