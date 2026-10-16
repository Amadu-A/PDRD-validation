# tests/architecture/test_decision_registry.py

"""Нулевой бюджет новых вызовов и независимость программного реестра от инфраструктуры."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "services/analysis-service/src/pdrd_analysis_service"


def test_registry_and_connections_have_no_model_network_storage_or_transport_access():
    """Предметные проверки не могут обращаться к VLM или создавать инфраструктурный обход."""
    for path in (
        PACKAGE / "domain/design_decisions.py",
        PACKAGE / "domain/signal_connections.py",
        PACKAGE / "domain/engineering_calculations.py",
        PACKAGE / "domain/engineering_checks.py",
        PACKAGE / "domain/project_dependencies.py",
        PACKAGE / "domain/requirement_applicability.py",
        PACKAGE / "application/decision_protocol.py",
        PACKAGE / "application/engineering_program.py",
    ):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(
                    part in module
                    for part in (
                        "infrastructure",
                        "transport",
                        "vision_model",
                        "httpx",
                        "fastapi",
                        "sqlalchemy",
                    )
                ), path
                if path.parent.name == "domain" and module.startswith(
                    "pdrd_analysis_service"
                ):
                    assert module.startswith("pdrd_analysis_service.domain."), path
            if isinstance(node, ast.Import):
                assert not {alias.name.split(".")[0] for alias in node.names} & {
                    "httpx",
                    "requests",
                    "socket",
                    "sqlalchemy",
                }, path
            if isinstance(node, ast.Call):
                assert (
                    not isinstance(node.func, ast.Attribute)
                    or node.func.attr != "generate_json"
                ), path


def test_electrical_benchmark_stays_offline_and_does_not_claim_positive_acceptance():
    """Контрольные ответы и конкретные модели приборов не попадают в рабочие промпты."""
    path = ROOT / "tests/fixtures/engineering/electrical-review-cases.json"
    benchmark = json.loads(path.read_text("utf-8"))
    assert benchmark["positive_benchmark_ready"] is False
    assert len({item["case_id"] for item in benchmark["cases"]}) == 12
    for source in PACKAGE.rglob("*.py"):
        content = source.read_text("utf-8")
        assert "electrical-review-cases.json" not in content
        assert "ECV1-L-RR-RS" not in content
