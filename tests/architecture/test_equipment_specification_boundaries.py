# tests/architecture/test_equipment_specification_boundaries.py

"""Таблицы извлекаются в Document Service и проходят прежние границы PDF pipeline."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def imports(path):
    """Читает настоящие зависимости Python-модуля через AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        item.module for item in ast.walk(tree) if isinstance(item, ast.ImportFrom)
    } | {
        alias.name
        for item in ast.walk(tree)
        if isinstance(item, ast.Import)
        for alias in item.names
    }


def test_specification_domain_has_no_pdf_or_provider_dependencies():
    """Нормализация не импортирует fitz/HTTP/VLM или соседний микросервис."""
    for service, package in (
        ("document-service", "pdrd_document_service"),
        ("analysis-service", "pdrd_analysis_service"),
    ):
        directory = ROOT / "services" / service / "src" / package / "domain"
        for path in directory.glob("*specification*.py"):
            modules = imports(path)
            assert not {"fitz", "httpx", "fastapi", "pydantic"} & modules
            assert all(
                not module or ".infrastructure" not in module for module in modules
            )
            assert (
                all(
                    not module or not module.startswith("pdrd_document_service")
                    for module in modules
                )
                if service == "analysis-service"
                else True
            )


def test_short_refinement_shares_production_model_cache_and_gpu_limiter(monkeypatch):
    """Текстовые порции используют тот же порт VLM и общий admission всех этапов."""
    from pdrd_analysis_service.application.limited_vision import LimitedVisionModel
    from pdrd_analysis_service.application.specification_refinement import (
        RefineSpecification,
    )
    from pdrd_analysis_service.core import container as wiring
    from pdrd_analysis_service.core.settings import Settings

    monkeypatch.setattr(wiring, "get_settings", lambda: Settings(_env_file=None))
    container = wiring.build_container()
    vision = container.understand_page.vision_model
    assert isinstance(vision._delegate, LimitedVisionModel)
    assert (
        RefineSpecification(vision).vision_model
        is container.check_page_against_norms.vision_model
    )
    assert vision is container.finalize_findings.vision_model


def test_pdf_workflow_enables_shared_tables_with_d_or_eq_and_keeps_one_stage():
    """D и EQ читают общий контракт; решение о надёжности остаётся в Python."""
    workflow = json.loads(
        (ROOT / "n8n/workflows/analysis-v2-pdf.json").read_text(encoding="utf-8")
    )
    nodes = {node["name"]: node for node in workflow["nodes"]}
    parameters = nodes["Document Extract PDF"]["parameters"]["bodyParameters"][
        "parameters"
    ]
    setting = next(
        parameter
        for parameter in parameters
        if parameter["name"] == "include_equipment_specification"
    )
    assert "use_equipment_web_search" in setting["value"]
    assert "use_document_context" in setting["value"] and "||" in setting["value"]
    assert [name for name in nodes if name == "Understand Pages Stage"] == [
        "Understand Pages Stage"
    ]
    assert (
        "equipment_specification"
        in nodes["Understand Pages Stage"]["parameters"]["body"]
    )
    assert (
        "specification: equipment.specification"
        in nodes["Merge Equipment Candidates"]["parameters"]["jsCode"]
    )
    assert not any(
        "specification_rows" in str(node["parameters"]) for node in nodes.values()
    )
