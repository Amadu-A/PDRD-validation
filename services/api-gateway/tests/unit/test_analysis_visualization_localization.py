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


def test_hypotheses_are_not_sent_to_automatic_localization() -> None:
    """Исходная VLM область гипотезы не становится подтверждённой рамкой."""
    targets = GetAnalysisVisualization._targets_for_page(
        findings=[
            {
                "finding_id": "p22-h1",
                "page": 22,
                "status": "hypothesis",
                "comment": "Позиция 8.12.34 повторяется.",
                "visual_regions": [
                    {"x_min": 100, "y_min": 100, "x_max": 130, "y_max": 130}
                ],
            },
            {"finding_id": "p22-f2", "page": 22, "status": "needs_review"},
        ],
        page_number=22,
    )

    assert [target.finding_id for target in targets] == ["p22-f2"]


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
    """VLM уточняет absence finding, но не уничтожает exact PDF anchor."""
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


def test_matcher_localizes_numeric_position_duplicates() -> None:
    """Чисто числовая позиция даёт exact multi-region localization."""
    matcher = FindingAnchorMatcher()

    location = matcher.locate(
        findings=(
            _target(
                evidence=(
                    "Позиционный номер 8.5.4 дублируется "
                    "в верхней и нижней частях схемы."
                ),
            ),
        ),
        text_words=(
            AnalysisTextWord(
                text="8.5.4",
                bbox=AnalysisBoundingBox(
                    x_min=120,
                    y_min=180,
                    x_max=170,
                    y_max=210,
                ),
                block_no=1,
                line_no=2,
                word_no=3,
            ),
            AnalysisTextWord(
                text="8.5.4",
                bbox=AnalysisBoundingBox(
                    x_min=620,
                    y_min=620,
                    x_max=670,
                    y_max=650,
                ),
                block_no=8,
                line_no=4,
                word_no=2,
            ),
        ),
    )[0]

    assert location.status == "located"
    assert location.method == "pdf_text"

    assert (
        len(
            location.regions,
        )
        == 2
    )

    assert tuple(region.label for region in location.regions) == (
        "8.5.4",
        "8.5.4",
    )


def test_matcher_localizes_all_three_confirmed_positions() -> None:
    """Три реальные подписи 8.5.4 не превращаются в unlocated."""
    matcher = FindingAnchorMatcher()
    words = tuple(
        AnalysisTextWord(
            text="8.5.4",
            bbox=AnalysisBoundingBox(
                x_min=100 + index * 200,
                y_min=180 + index * 100,
                x_max=150 + index * 200,
                y_max=210 + index * 100,
            ),
            block_no=index,
            line_no=1,
            word_no=1,
        )
        for index in range(3)
    )

    location = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p22-dpos-8-5-4",
                comment="Проверить повторное позиционное обозначение 8.5.4.",
                evidence="Подпись 8.5.4 встречается три раза.",
            ),
        ),
        text_words=words,
    )[0]

    assert location.status == "located"
    assert location.method == "pdf_text"
    assert len(location.regions) == 3
    assert all(region.label == "8.5.4" for region in location.regions)


def test_matcher_does_not_use_plain_integer_as_strong_anchor() -> None:
    """Обычный номер страницы не должен давать ложную localization."""
    matcher = FindingAnchorMatcher()

    location = matcher.locate(
        findings=(
            _target(
                evidence="На листе указано значение 3.",
            ),
        ),
        text_words=(
            AnalysisTextWord(
                text="3",
                bbox=AnalysisBoundingBox(
                    x_min=900,
                    y_min=20,
                    x_max=920,
                    y_max=40,
                ),
                block_no=1,
                line_no=1,
                word_no=1,
            ),
        ),
    )[0]

    assert location.status == "unlocated"
