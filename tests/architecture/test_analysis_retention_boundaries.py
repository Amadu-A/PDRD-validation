# tests/architecture/test_analysis_retention_boundaries.py

"""Границы сервисов, закрытого канала очистки и стандартного запуска стека."""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def test_cleanup_uses_existing_dispatcher_and_only_analysis_volume():
    """Очистка использует существующий outbox, не монтируя чужие постоянные артефакты."""
    compose = (ROOT / "compose.yaml").read_text("utf-8")
    outbox = compose.split("  api-gateway-outbox:", 1)[1].split(
        "  api-gateway-worker:", 1
    )[0]
    assert "analysis_artifacts:/data/analyses" in outbox
    assert "experience_crops:" not in outbox and "normative_documents:" not in outbox
    dispatcher = (
        ROOT
        / "services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/dispatcher.py"
    ).read_text("utf-8")
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
        and node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id == "run_retention"
        for node in ast.walk(ast.parse(dispatcher))
    )
    assert "retention_task.cancel()" in dispatcher


def test_knowledge_cleanup_does_not_cross_service_boundary_or_expose_public_route():
    """Gateway использует HTTP-порт, Knowledge проверяет отдельный серверный ключ."""
    adapter = (
        ROOT
        / "services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/analysis_retention.py"
    ).read_text("utf-8")
    assert "pdrd_knowledge_service" not in adapter and "sqlalchemy" not in adapter
    assert "X-PDRD-Retention-Key" in adapter
    route = (
        ROOT
        / "services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/technical_assignments.py"
    ).read_text("utf-8")
    assert "compare_digest(retention_key, expected)" in route
    public = (
        ROOT
        / "services/api-gateway/src/pdrd_api_gateway/transport/http/routers/technical_assignments.py"
    ).read_text("utf-8")
    assert "@router.delete" not in public


def test_deployment_keeps_standard_startup_and_checks_migrations():
    """У проекта остаётся стандартная точка сборки/миграций и проверка текущей схемы."""
    assert not (ROOT / "ops/deploy-analysis-history.sh").exists()
    check = (ROOT / "scripts/check-stack.sh").read_text("utf-8")
    assert 'check_migrations_current "api-gateway"' in check
    assert 'check_migrations_current "knowledge-service"' in check


def test_retention_migrations_have_one_head_and_preserve_existing_chain():
    """Настоящий граф Alembic запрещает дубль ревизии и ответвление от прежнего head."""
    for service in ("api-gateway", "knowledge-service"):
        graph = ScriptDirectory.from_config(
            Config(str(ROOT / "services" / service / "alembic.ini"))
        )
        assert graph.get_heads() == ["20261006_0007"]
        revisions = list(graph.walk_revisions())
        assert len({node.revision for node in revisions}) == len(revisions)
        assert graph.get_revision("20261006_0007").down_revision == "20261006_0006"
