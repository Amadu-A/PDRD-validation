# tests/architecture/test_normative_atomic_boundaries.py

"""Направление зависимостей, контракты, отсутствие нового inference и безопасная поставка этапа A."""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from pdrd_api_gateway.transport.http.schemas.normative_requirements import (
    RecordNormativeStatusRequest as PublicProof,
)
from pdrd_knowledge_service.transport.http.schemas.normative_requirements import (
    RecordNormativeStatusRequest as InternalProof,
)

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "services/knowledge-service/src/pdrd_knowledge_service"


def test_atomic_domain_and_application_have_no_inference_storage_or_cross_service_dependencies():
    """Полный корпус атомизируется программно, не размножая вызовы модели по пунктам."""
    for relative in (
        "domain/normative_requirements.py",
        "domain/normative_requirement_extraction.py",
        "application/use_cases/normative_requirements.py",
    ):
        tree = ast.parse((KNOWLEDGE / relative).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    (
                        "httpx",
                        "sqlalchemy",
                        "pdrd_analysis_service",
                        "pdrd_api_gateway",
                        "pdrd_knowledge_service.infrastructure",
                    )
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {
                    "generate_json",
                    "embed",
                    "upsert",
                    "delete_by_filter",
                    "send_task",
                }
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "__import__"}


def test_status_command_schemas_match_and_reject_server_identity_fields():
    """Gateway и Knowledge не расходятся в датах, модальностях и границах входной команды."""
    public, internal = (
        PublicProof.model_json_schema(),
        InternalProof.model_json_schema(),
    )
    for field in ("properties", "required", "additionalProperties"):
        assert public[field] == internal[field]
    assert public["additionalProperties"] is False
    assert not {"verified_by", "source_sha256", "document_id", "recorded_at"} & set(
        public["properties"]
    )


def test_atomic_migration_extends_existing_catalog_without_data_rewrites_or_extra_services():
    """Обычный upgrade создаёт пустой каталог; данные текущего корпуса не переиндексируются."""
    config = Config(str(ROOT / "services/knowledge-service/alembic.ini"))
    graph = ScriptDirectory.from_config(config)
    assert graph.get_heads() == ["20261020_0009"]
    assert graph.get_revision("20261020_0009").down_revision == "20261008_0008"
    path = (
        ROOT
        / "services/knowledge-service/alembic/versions/20261020_0009_normative_requirements.py"
    )
    tree = ast.parse(path.read_text("utf-8"))
    upgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    calls = {
        node.func.attr
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls <= {
        "create_table",
        "Column",
        "Uuid",
        "ForeignKey",
        "String",
        "Boolean",
        "CheckConstraint",
        "create_index",
        "text",
        "Integer",
        "UniqueConstraint",
    }
    assert "create_table" in calls and not calls & {
        "execute",
        "drop_table",
        "alter_column",
    }
    celery = (KNOWLEDGE / "infrastructure/messaging/celery_app.py").read_text("utf-8")
    assert '"pdrd.knowledge.normative_requirements.index"' in celery
    assert '"queue": broker_settings.queue_name' in celery
