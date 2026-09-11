# tests/architecture/test_analysis_visualization.py

"""Architecture guards foundation визуализации findings."""

import ast
from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

VISUALIZATION_JS = (
    REPOSITORY_ROOT
    / "frontend"
    / "src"
    / "js"
    / "features"
    / "analysis"
    / "visualization.js"
)

LOCALIZATION_USE_CASE = (
    REPOSITORY_ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "application"
    / "use_cases"
    / "finding_localization.py"
)

VISUALIZATION_RENDERER = (
    REPOSITORY_ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "infrastructure"
    / "visualization.py"
)


def test_frontend_visualization_is_dom_safe() -> None:
    """Overlay использует DOM API и не вставляет model text через innerHTML."""
    source = VISUALIZATION_JS.read_text(
        encoding="utf-8",
    )

    required = (
        "finding?.location",
        "location.bbox",
        "x_min",
        "y_min",
        "x_max",
        "y_max",
        "createElementNS",
        "polyline",
        "analysis-result__bbox",
        "analysis-result__annotation",
        "analysis-result__annotation-tooltip",
        "source.text",
        "textContent",
    )

    missing = [marker for marker in required if marker not in source]

    assert not missing, "\n".join(
        missing,
    )

    assert "innerHTML" not in source


def test_localization_use_case_is_self_contained() -> None:
    """Foundation не зависит от ещё не внесённых tracked integration edits."""
    source = LOCALIZATION_USE_CASE.read_text(
        encoding="utf-8",
    )

    tree = ast.parse(
        source,
    )

    imported_modules = {
        node.module
        for node in ast.walk(
            tree,
        )
        if isinstance(
            node,
            ast.ImportFrom,
        )
        and node.module is not None
    }

    assert "pdrd_analysis_service.application.json_schemas" not in imported_modules

    assert "pdrd_analysis_service.application.prompts" not in imported_modules

    assert "def build_finding_localization_schema(" in source
    assert "def build_finding_localization_prompt(" in source
    assert "FindingLocation.unlocated" in source


def test_pdf_preview_renderer_uses_single_document_service_request() -> None:
    """Renderer переиспользует существующий batch PDF extract endpoint."""
    source = VISUALIZATION_RENDERER.read_text(
        encoding="utf-8",
    )

    assert "/internal/v1/pdf/extract" in source

    tree = ast.parse(
        source,
    )

    post_calls = [
        node
        for node in ast.walk(
            tree,
        )
        if isinstance(
            node,
            ast.Call,
        )
        and isinstance(
            node.func,
            ast.Attribute,
        )
        and node.func.attr == "post"
    ]

    assert (
        len(
            post_calls,
        )
        == 1
    )
