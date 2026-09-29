# tests/architecture/test_experience_catalog_boundaries.py

"""Границы каталога, закрытый HTTP-канал, своё хранилище и неизменяемая миграция."""

import ast
from pathlib import Path

from pdrd_experience_service.infrastructure.database.catalog_models import (
    CatalogEventModel,
    CatalogExampleModel,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_domain_and_application_have_no_runtime_or_foreign_service_imports():
    """PDF/SQL/HTTP остаются в адаптерах, чужие бизнес-пакеты не импортируются."""
    for service, package in [
        ("experience-service", "pdrd_experience_service"),
        ("api-gateway", "pdrd_api_gateway"),
        ("document-service", "pdrd_document_service"),
    ]:
        root = ROOT / "services" / service / "src" / package
        for layer in ("domain", "application"):
            for path in (root / layer).rglob("*.py"):
                if not any(
                    word in path.name for word in ("catalog", "experience", "crop")
                ):
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                imports = [
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                ]
                imports += [
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                ]
                assert not any(
                    module.split(".")[0]
                    in {"httpx", "fastapi", "sqlalchemy", "fitz", "PIL"}
                    for module in imports
                ), path
                assert not any(
                    ".infrastructure" in module or ".transport" in module
                    for module in imports
                ), path
                assert not any(
                    module.startswith("pdrd_") and not module.startswith(package + ".")
                    for module in imports
                ), path


def test_catalog_owns_its_tables_and_events_have_composite_revision_key():
    """Пример и журнал принадлежат только Experience, binary PDF в таблицах нет."""
    for model in (CatalogExampleModel, CatalogEventModel):
        sql = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert model.__table__.schema == "experience"
        assert "JSONB" in sql and "BYTEA" not in sql
    assert [column.name for column in CatalogEventModel.__table__.primary_key] == [
        "example_id",
        "revision",
    ]


def test_all_gateway_catalog_routes_require_closed_review_channel():
    """Список, картинки и выгрузка защищаются до вызова сценариев."""
    path = (
        ROOT
        / "services/api-gateway/src/pdrd_api_gateway/transport/http/routers/experience.py"
    )
    source = path.read_text(encoding="utf-8")
    assert "Depends(require_review_channel)" in source
    tree = ast.parse(source)
    endpoints = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in {"get", "post", "patch", "delete"}
            for decorator in node.decorator_list
        )
    ]
    assert len(endpoints) == 8
    assert all(
        any(
            argument.arg == "container"
            and isinstance(argument.annotation, ast.Name)
            and argument.annotation.id == "Container"
            for argument in node.args.args
        )
        for node in endpoints
    )


def test_catalog_frontend_is_modular_and_uses_real_crop_urls():
    """Controller, network и dialogs не сливаются в app/page; серверные примеры не вымышленные."""
    root = ROOT / "frontend/src/js/features/experience"
    for name in (
        "page.js",
        "api.js",
        "rows.js",
        "dialogs.js",
        "server-page.js",
        "capture.js",
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 250
        assert "innerHTML" not in source
    assert "DEMO_EXAMPLES" not in (root / "server-page.js").read_text(encoding="utf-8")
    assert "api.imageUrl" in (root / "rows.js").read_text(encoding="utf-8")
    capture = (root / "capture.js").read_text(encoding="utf-8")
    assert "document." not in capture
    assert "fetch(" not in capture


def test_review_export_is_the_only_experience_capture_action_in_ui():
    """Review не создаёт отдельную кнопку сохранения и не отправляет пользователя в SSH."""
    root = ROOT / "frontend/src/js/features"
    persistence = (root / "review/persistence.js").read_text(encoding="utf-8")
    export = (root / "review/pdf-controls.js").read_text(encoding="utf-8")
    assert "mountExperienceCapture" not in persistence
    assert "captureApprovedExperience" in export
    for directory in ("review", "experience"):
        for source in (root / directory).glob("*.js"):
            text = source.read_text(encoding="utf-8")
            assert "SSH-туннель" not in text
            assert "127.0.0.1:8081" not in text
            assert "Сохранить в базу опыта" not in text


def test_windows_quality_commands_disable_git_pager():
    """Регрессия падения less/MSYS в предоставленном логе Windows."""
    source = (ROOT / "ops/check-quality.ps1").read_text(encoding="utf-8-sig")
    assert '$gitArguments = @("--no-pager",' in source
