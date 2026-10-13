# tests/architecture/test_equipment_review_presentation_boundaries.py

"""Новые средства представления не создают отдельный Review или сетевой клиент сервисов."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_finding_filters_and_evidence_navigation_have_no_review_or_private_network():
    """Чистый выбор карточек и переход по листу не записывают решения и не вызывают сервис."""
    directory = ROOT / "frontend/src/js/features/analysis"
    for filename in ("finding-list.js", "finding-provenance.js"):
        source = (directory / filename).read_text(encoding="utf-8")
        for forbidden in (
            "fetch(",
            "XMLHttpRequest",
            "localStorage",
            "sessionStorage",
            "new WebSocket",
            "/internal/v1/",
            "X-Internal-Key",
            "createReviewState(",
            "createReviewController(",
        ):
            assert forbidden not in source, (filename, forbidden)


def test_output_budget_port_keeps_application_independent_of_http_and_storage():
    """Причина переполнения переносится через доменные метрики, без импорта адаптера."""
    path = (
        ROOT
        / "services/analysis-service/src/pdrd_analysis_service/application/ports/vision_model.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert all(
        not name.startswith(
            ("httpx", "fastapi", "sqlalchemy", "pdrd_analysis_service.infrastructure")
        )
        for name in imports
    )
    definitions = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert isinstance(definitions["VisionModelOutputBudgetError"].bases[0], ast.Name)
    assert definitions["VisionModelOutputBudgetError"].bases[0].id == "VisionModelError"
