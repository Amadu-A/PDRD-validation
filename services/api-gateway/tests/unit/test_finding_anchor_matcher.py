# services/api-gateway/tests/unit/test_finding_anchor_matcher.py

"""Unit tests deterministic PDF finding anchor matcher."""

from pdrd_api_gateway.application.finding_anchor_matcher import (
    FindingAnchorMatcher,
)
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingTarget,
    AnalysisTextWord,
)


def word(
    *,
    text: str,
    x_min: int,
    y_min: int,
    x_max: int,
    y_max: int,
    block_no: int,
    line_no: int,
    word_no: int,
) -> AnalysisTextWord:
    """Создаёт synthetic PDF word."""
    return AnalysisTextWord(
        text=text,
        bbox=AnalysisBoundingBox(
            x_min=x_min,
            y_min=y_min,
            x_max=x_max,
            y_max=y_max,
        ),
        block_no=block_no,
        line_no=line_no,
        word_no=word_no,
    )


def test_matches_exact_engineering_designations() -> None:
    """XT1/XT2 находятся deterministic без VLM."""
    matcher = FindingAnchorMatcher()

    locations = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="F-1",
                comment=("Отсутствует маркировка разъёмов XT1 и XT2."),
                evidence=("XT1 и XT2 не имеют видимой маркировки."),
            ),
        ),
        text_words=(
            word(
                text="XT1",
                x_min=100,
                y_min=200,
                x_max=130,
                y_max=220,
                block_no=1,
                line_no=1,
                word_no=1,
            ),
            word(
                text="XT2",
                x_min=300,
                y_min=200,
                x_max=330,
                y_max=220,
                block_no=2,
                line_no=1,
                word_no=1,
            ),
        ),
    )

    location = locations[0]

    assert location.status == "located"

    assert location.method == "pdf_text"

    assert (
        len(
            location.regions,
        )
        == 2
    )

    assert {region.label for region in location.regions} == {
        "XT1",
        "XT2",
    }


def test_exact_boundary_does_not_match_p1_inside_p1a() -> None:
    """Designation P1 не должен случайно совпасть с P1A."""
    matcher = FindingAnchorMatcher()

    locations = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="F-1",
                comment=("Проверить клемму P1."),
                evidence=("P1 не имеет маркировки."),
            ),
        ),
        text_words=(
            word(
                text="P1A",
                x_min=100,
                y_min=100,
                x_max=140,
                y_max=120,
                block_no=1,
                line_no=1,
                word_no=1,
            ),
            word(
                text="P1",
                x_min=200,
                y_min=100,
                x_max=230,
                y_max=120,
                block_no=1,
                line_no=2,
                word_no=1,
            ),
        ),
    )

    regions = locations[0].regions

    assert (
        len(
            regions,
        )
        == 1
    )

    assert regions[0].label == "P1"


def test_adjacent_anchor_words_are_grouped() -> None:
    """LEAD 1B образует одну visual region."""
    matcher = FindingAnchorMatcher()

    locations = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="F-1",
                comment=("Маркировка LEAD 1B отсутствует."),
                evidence=("LEAD 1B расположен на проверяемой схеме."),
            ),
        ),
        text_words=(
            word(
                text="LEAD",
                x_min=100,
                y_min=200,
                x_max=150,
                y_max=220,
                block_no=1,
                line_no=1,
                word_no=4,
            ),
            word(
                text="1B",
                x_min=155,
                y_min=200,
                x_max=175,
                y_max=220,
                block_no=1,
                line_no=1,
                word_no=5,
            ),
        ),
    )

    location = locations[0]

    assert (
        len(
            location.regions,
        )
        == 1
    )

    assert location.regions[0].label == "LEAD 1B"


def test_normative_words_are_not_used_as_visual_anchors() -> None:
    """Слово ГОСТ не должно привязывать finding к случайному title block."""
    matcher = FindingAnchorMatcher()

    locations = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="F-1",
                comment=("Нарушены требования ГОСТ."),
                evidence=("Есть несоответствие ГОСТ."),
            ),
        ),
        text_words=(
            word(
                text="ГОСТ",
                x_min=100,
                y_min=100,
                x_max=150,
                y_max=120,
                block_no=1,
                line_no=1,
                word_no=1,
            ),
        ),
    )

    assert locations[0].status == "unlocated"
