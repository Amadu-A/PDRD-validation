# services/api-gateway/tests/unit/test_analysis_visualization_localization.py

"""Focused tests localization v2: homoglyphs и VLM refinement."""

from pdrd_api_gateway.application.finding_anchor_matcher import (
    FindingAnchorMatcher,
)
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingLocation,
    AnalysisFindingTarget,
    AnalysisTextWord,
    AnalysisVisualRegion,
)
from pdrd_api_gateway.application.use_cases.get_analysis_visualization import (
    GetAnalysisVisualization,
)


def _target(
    *,
    finding_id: str = "F-1",
    comment: str = "Замечание",
    evidence: str = "Видимый факт.",
) -> AnalysisFindingTarget:
    """Создаёт finding target."""
    return AnalysisFindingTarget(
        finding_id=finding_id,
        comment=comment,
        evidence=evidence,
    )


def _located(
    *,
    finding_id: str,
    method: str,
    x_min: int,
) -> AnalysisFindingLocation:
    """Создаёт synthetic located result."""
    return AnalysisFindingLocation.located(
        finding_id=finding_id,
        regions=(
            AnalysisVisualRegion(
                bbox=AnalysisBoundingBox(
                    x_min=x_min,
                    y_min=100,
                    x_max=x_min + 100,
                    y_max=200,
                ),
                source=method,
                confidence=0.9,
                label=None,
            ),
        ),
        confidence=0.9,
        method=method,
    )


def test_matcher_normalizes_latin_and_cyrillic_engineering_tags() -> None:
    """Latin T1.1 в finding совпадает с кириллической Т1.1 PDF geometry."""
    matcher = FindingAnchorMatcher()

    location = matcher.locate(
        findings=(
            _target(
                evidence=("Схема показывает линию T1.1 к котельной."),
            ),
        ),
        text_words=(
            AnalysisTextWord(
                text="Т1.1,",
                bbox=AnalysisBoundingBox(
                    x_min=120,
                    y_min=240,
                    x_max=180,
                    y_max=270,
                ),
                block_no=1,
                line_no=2,
                word_no=3,
            ),
        ),
    )[0]

    assert location.status == "located"
    assert location.method == "pdf_text"
    assert (
        len(
            location.regions,
        )
        == 1
    )
    assert location.regions[0].label == "Т1.1,"


def test_absence_finding_requires_visual_refinement() -> None:
    """Finding об отсутствии должен уточняться VLM по evidence area."""
    target = _target(
        evidence=("Схема T1.1 не содержит изображения запорного клапана."),
    )

    assert (
        GetAnalysisVisualization._requires_visual_refinement(
            target,
        )
        is True
    )

    ordinary = _target(
        evidence=("Разъём XT1 расположен в верхней части схемы."),
    )

    assert (
        GetAnalysisVisualization._requires_visual_refinement(
            ordinary,
        )
        is False
    )


def test_refinement_prefers_vlm_but_keeps_pdf_anchor_as_fallback() -> None:
    """VLM уточняет absence finding, но не может уничтожить exact PDF anchor."""
    target = _target()

    deterministic = _located(
        finding_id="F-1",
        method="pdf_text",
        x_min=100,
    )

    vlm = _located(
        finding_id="F-1",
        method="vlm",
        x_min=500,
    )

    refined = GetAnalysisVisualization._merge_location(
        target=target,
        deterministic=deterministic,
        fallback=vlm,
        prefer_vlm=True,
    )

    assert refined.method == "vlm"
    assert refined.regions[0].bbox.x_min == 500

    safe_fallback = GetAnalysisVisualization._merge_location(
        target=target,
        deterministic=deterministic,
        fallback=(
            AnalysisFindingLocation.unlocated(
                finding_id="F-1",
            )
        ),
        prefer_vlm=True,
    )

    assert safe_fallback.method == "pdf_text"
    assert safe_fallback.regions[0].bbox.x_min == 100

    ordinary = GetAnalysisVisualization._merge_location(
        target=target,
        deterministic=deterministic,
        fallback=vlm,
        prefer_vlm=False,
    )

    assert ordinary.method == "pdf_text"
