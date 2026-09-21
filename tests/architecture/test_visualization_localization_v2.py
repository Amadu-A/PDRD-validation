# tests/architecture/test_visualization_localization_v2.py

"""Architecture guards improved localization и finding detail tooltip."""

from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

GATEWAY_MATCHER = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "application"
    / "finding_anchor_matcher.py"
)

GATEWAY_VISUALIZATION = (
    ROOT
    / "services"
    / "api-gateway"
    / "src"
    / "pdrd_api_gateway"
    / "application"
    / "use_cases"
    / "get_analysis_visualization.py"
)

ANALYSIS_LOCALIZATION = (
    ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "application"
    / "use_cases"
    / "finding_localization.py"
)

FRONTEND_VISUALIZATION = (
    ROOT / "frontend" / "src" / "js" / "features" / "analysis" / "visualization.js"
)

FRONTEND_DETAIL_CSS = (
    ROOT / "frontend" / "src" / "css" / "blocks" / "analysis-annotation-detail.css"
)

FRONTEND_STYLE = ROOT / "frontend" / "src" / "css" / "style.css"


def _read(
    path: Path,
) -> str:
    """Читает source как UTF-8."""
    return path.read_text(
        encoding="utf-8",
    )


def test_matcher_normalizes_engineering_homoglyphs() -> None:
    """Latin/Cyrillic engineering tags должны матчиться deterministic."""
    matcher = _read(
        GATEWAY_MATCHER,
    )

    assert "_HOMOGLYPH_TRANSLATION" in matcher

    assert '"Т": "T"' in matcher

    assert ".translate(" in matcher

    assert "_HOMOGLYPH_TRANSLATION" in matcher


def test_visualization_refines_absence_findings_without_losing_pdf_anchor() -> None:
    """Absence finding идёт в VLM refinement с deterministic fallback."""
    source = _read(
        GATEWAY_VISUALIZATION,
    )

    assert "_VISUAL_REFINEMENT_MARKERS" in source

    assert "refinement_targets" in source

    assert "_requires_visual_refinement" in source

    assert "prefer_vlm" in source

    assert 'deterministic.status == "located"' in source


def test_vlm_prompt_localizes_absence_evidence_area() -> None:
    """VLM не должен автоматически объявлять отсутствующий object unlocated."""
    source = _read(
        ANALYSIS_LOCALIZATION,
    )

    assert "ОБЛАСТЬ ДОКАЗАТЕЛЬСТВА ОТСУТСТВИЯ" in source

    assert ("НЕ придумывай bbox самого отсутствующего объекта") in source

    assert ("status=unlocated, bbox=null используй только") in source


def test_frontend_has_accessible_full_finding_tooltip() -> None:
    """Compact card содержит keyboard-accessible info control."""
    visualization = _read(
        FRONTEND_VISUALIZATION,
    )

    css = _read(
        FRONTEND_DETAIL_CSS,
    )

    style = _read(
        FRONTEND_STYLE,
    )

    required_js = (
        "findingDisplayText",
        "createFindingDetailControl",
        "analysis-result__annotation-info-button",
        "analysis-result__annotation-detail-tooltip",
        "aria-label",
        "aria-describedby",
        "Основание на листе",
        "Нормативное основание",
        "Рекомендация",
        "Уверенность",
        "Локализация",
    )

    missing = [marker for marker in required_js if marker not in visualization]

    assert not missing, "\n".join(
        missing,
    )

    assert "innerHTML" not in visualization

    assert ":focus-within" in css

    assert ":focus-visible" in css

    assert ('@import url("./blocks/analysis-annotation-detail.css");') in style
