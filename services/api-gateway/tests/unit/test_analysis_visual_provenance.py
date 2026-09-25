# services/api-gateway/tests/unit/test_analysis_visual_provenance.py

"""Regression tests saved VLM regions and scoped PDF refinement."""

from pdrd_api_gateway.application.finding_anchor_matcher import (
    FindingAnchorMatcher,
)
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingTarget,
    AnalysisTextWord,
    AnalysisVisualRegion,
)
from pdrd_api_gateway.application.use_cases.get_analysis_visualization import (
    GetAnalysisVisualization,
)


def _word(
    *,
    text: str,
    x_min: int,
    y_min: int,
    x_max: int,
    y_max: int,
    word_no: int = 1,
) -> AnalysisTextWord:
    """Создаёт synthetic positioned PDF word."""
    return AnalysisTextWord(
        text=text,
        bbox=AnalysisBoundingBox(
            x_min=x_min,
            y_min=y_min,
            x_max=x_max,
            y_max=y_max,
        ),
        block_no=1,
        line_no=1,
        word_no=word_no,
    )


def test_saved_vlm_regions_prevent_global_laser_anchors() -> None:
    """При наличии VLM bbox global text matcher не размечает весь лист."""
    matcher = FindingAnchorMatcher()

    target = AnalysisFindingTarget(
        finding_id="F-1",
        comment=("Несогласованность количества резервуаров."),
        evidence=("На схеме и в подписи показано разное количество."),
        visual_regions=(
            AnalysisVisualRegion(
                bbox=AnalysisBoundingBox(
                    x_min=400,
                    y_min=300,
                    x_max=600,
                    y_max=450,
                ),
                source="analysis_vlm",
                confidence=0.94,
                label="резервуары",
            ),
        ),
    )

    locations = matcher.locate(
        findings=(target,),
        text_words=(
            _word(
                text="Резервуар",
                x_min=50,
                y_min=50,
                x_max=120,
                y_max=70,
            ),
            _word(
                text="Резервуар",
                x_min=850,
                y_min=850,
                x_max=920,
                y_max=870,
            ),
        ),
    )

    location = locations[0]

    assert location.method == "analysis_vlm"

    assert (
        len(
            location.regions,
        )
        == 1
    )

    assert location.regions[0].bbox == target.visual_regions[0].bbox


def test_single_sheet_numbers_are_refined_only_inside_vlm_regions() -> None:
    """Одиночные 9 и 5 допустимы только внутри уже известных VLM regions."""
    matcher = FindingAnchorMatcher()

    target = AnalysisFindingTarget(
        finding_id="F-9",
        comment=("Номер страницы 9 не совпадает с номером листа 5."),
        evidence=("В правом верхнем углу указан 9, в основной надписи указан 5."),
        visual_regions=(
            AnalysisVisualRegion(
                bbox=AnalysisBoundingBox(
                    x_min=900,
                    y_min=0,
                    x_max=970,
                    y_max=80,
                ),
                source="analysis_vlm",
                confidence=0.9,
                label="номер страницы 9",
            ),
            AnalysisVisualRegion(
                bbox=AnalysisBoundingBox(
                    x_min=900,
                    y_min=900,
                    x_max=980,
                    y_max=1000,
                ),
                source="analysis_vlm",
                confidence=0.9,
                label="Лист 5",
            ),
        ),
    )

    locations = matcher.locate(
        findings=(target,),
        text_words=(
            _word(
                text="9",
                x_min=930,
                y_min=20,
                x_max=940,
                y_max=40,
            ),
            _word(
                text="5",
                x_min=935,
                y_min=945,
                x_max=945,
                y_max=965,
            ),
            _word(
                text="5",
                x_min=100,
                y_min=200,
                x_max=110,
                y_max=220,
            ),
        ),
    )

    location = locations[0]

    assert location.method == "analysis_vlm"

    assert (
        len(
            location.regions,
        )
        == 2
    )

    assert location.regions[0].source == "analysis_vlm+pdf_text"

    assert location.regions[1].source == "analysis_vlm+pdf_text"

    assert location.regions[1].bbox.x_min > 800


def test_cyrillic_ne_is_not_promoted_to_ascii_he_anchor() -> None:
    """Русское слово НЕ больше не становится engineering anchor HE."""
    matcher = FindingAnchorMatcher()

    location = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="F-NE",
                comment="Значение не соответствует номеру листа.",
                evidence="На листе слово не относится к объекту.",
            ),
        ),
        text_words=(
            _word(
                text="не",
                x_min=100,
                y_min=200,
                x_max=130,
                y_max=220,
            ),
        ),
    )[0]

    assert location.status == "unlocated"


def test_result_parser_restores_saved_visual_regions() -> None:
    """Final result visual_regions превращаются в AnalysisFindingTarget."""
    targets = GetAnalysisVisualization._targets_for_page(
        findings=[
            {
                "finding_id": "F-1",
                "page": 9,
                "comment": "Несовпадение номера.",
                "evidence": "9 и 5.",
                "visual_regions": [
                    {
                        "x_min": 900,
                        "y_min": 10,
                        "x_max": 960,
                        "y_max": 60,
                        "confidence": 0.93,
                        "label": "номер 9",
                    }
                ],
            }
        ],
        page_number=9,
    )

    assert (
        len(
            targets,
        )
        == 1
    )

    assert (
        len(
            targets[0].visual_regions,
        )
        == 1
    )

    assert targets[0].visual_regions[0].source == "analysis_vlm"
