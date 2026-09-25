# services/api-gateway/tests/unit/test_stage5_visual_review.py

"""Регрессии: точные позиции 8.9.2/8.9.3 и широкие области резервуаров."""

import hashlib
import json

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
from pdrd_api_gateway.application.ports.analysis_visualization_cache import (
    AnalysisVisualizationLocationPage,
)
from pdrd_api_gateway.application.use_cases.get_analysis_visualization import (
    GetAnalysisVisualization,
)


def _word(text: str, x: int, y: int, no: int) -> AnalysisTextWord:
    return AnalysisTextWord(
        text=text,
        bbox=AnalysisBoundingBox(x_min=x, y_min=y, x_max=x + 22, y_max=y + 19),
        block_no=no,
        line_no=1,
        word_no=1,
    )


def _region(x: int, y: int, w: int, h: int, label: str) -> AnalysisVisualRegion:
    return AnalysisVisualRegion(
        bbox=AnalysisBoundingBox(x_min=x, y_min=y, x_max=x + w, y_max=y + h),
        source="analysis_vlm",
        confidence=0.9,
        label=label,
    )


def test_duplicate_8_9_2_uses_two_exact_pdf_labels_not_wrong_vlm_rectangles() -> None:
    """Два 8.9.2 уточняются по точным словам PDF, минуя ошибочный 8.9.1."""
    matcher = FindingAnchorMatcher()
    result = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p22-f3",
                comment=(
                    "Дублирование позиционного обозначения 8.9.2 "
                    "для насосов 8.2.1 и 8.2.2."
                ),
                evidence="Номер 8.9.2 встречается дважды.",
                visual_regions=(
                    _region(620, 440, 40, 30, "8.9.2 — ошибочная VLM область"),
                    _region(620, 600, 40, 30, "8.9.2 — ошибочная VLM область"),
                ),
            ),
        ),
        text_words=(
            _word("8.9.1", 640, 430, 1),
            _word("8.9.2", 847, 348, 2),
            _word("8.9.2", 660, 617, 3),
            _word("8.9.3", 415, 222, 4),
        ),
    )[0]
    assert result.status == "located"
    assert result.method == "pdf_text"
    assert len(result.regions) == 2
    assert all(region.label == "8.9.2" for region in result.regions)
    x_positions = [region.bbox.x_min for region in result.regions]
    # Порядок чтения: сначала верхняя, затем нижняя подпись.
    assert x_positions == [835, 648]
    assert all(region.bbox.x_min != 608 for region in result.regions)


def test_duplicate_without_two_distinct_text_labels_is_unlocated() -> None:
    """Одна найденная подпись не подтверждает две области повтора."""
    matcher = FindingAnchorMatcher()
    result = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p22-dpos-8-9-3",
                comment="Проверить повторное позиционное обозначение 8.9.3.",
                evidence="Две отдельные подписи.",
                visual_regions=(_region(620, 400, 40, 30, "8.9.3"),),
            ),
        ),
        text_words=(_word("8.9.3", 415, 222, 1),),
    )[0]
    assert result.status == "unlocated"


def test_wide_semantic_reservoir_areas_do_not_shrink_to_unrelated_number() -> None:
    """Широкие области резервуаров не сводятся к посторонней цифре."""
    matcher = FindingAnchorMatcher()
    originals = (
        _region(200, 250, 250, 150, "Один резервуар на плане"),
        _region(250, 650, 200, 200, "Два резервуара на схеме"),
    )
    result = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p23-f1",
                comment="Сравнить количество резервуаров между планом и схемой.",
                evidence="В разных областях указаны различные количества.",
                visual_regions=originals,
            ),
        ),
        text_words=(_word("резервуаров", 230, 380, 1),),
    )[0]
    assert result.status == "located"
    assert result.regions == originals


def test_wide_reservoir_area_does_not_shrink_to_matching_count() -> None:
    """Число в подписи сравнения не заменяет область самого резервуара."""
    original = _region(200, 250, 250, 150, "2 резервуара на плане")
    result = FindingAnchorMatcher().locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p23-f1",
                comment="На плане 2 резервуара, на схеме 4.",
                evidence="Сравнить количество резервуаров.",
                visual_regions=(original,),
            ),
        ),
        text_words=(_word("2", 410, 350, 1),),
    )[0]
    assert result.regions == (original,)


def test_compact_reservoir_area_is_not_treated_as_number_field() -> None:
    """Даже тесная область объекта не сводится к цифре его количества."""
    original = _region(400, 340, 60, 60, "2 резервуара")
    result = FindingAnchorMatcher().locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p23-f1",
                comment="На плане 2 резервуара, на схеме 4.",
                evidence="Сравнить количество резервуаров.",
                visual_regions=(original,),
            ),
        ),
        text_words=(_word("2", 410, 350, 1),),
    )[0]
    assert result.regions == (original,)


def test_unlabelled_vlm_area_keeps_its_geometry() -> None:
    """Описание finding не даёт права сдвигать неподписанную VLM-область."""
    original = _region(200, 250, 250, 150, "")
    result = FindingAnchorMatcher().locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p22-f1",
                comment="Номера 8.5.1 и 8.5.5 отличаются.",
                evidence="Сравнить датчики в двух узлах.",
                visual_regions=(original,),
            ),
        ),
        text_words=(_word("8.5.1", 410, 350, 1),),
    )[0]
    assert result.regions == (original,)


def test_same_id_changed_evidence_invalidates_visualization_cache() -> None:
    """Изменение evidence инвалидирует кэш при прежнем finding ID."""
    first = AnalysisFindingTarget(
        finding_id="p23-f1", comment="Резервуары", evidence="План", visual_regions=()
    )
    changed = AnalysisFindingTarget(
        finding_id="p23-f1", comment="Котельные", evidence="Схема", visual_regions=()
    )
    cached = AnalysisVisualizationLocationPage(
        page_number=23,
        locations=(AnalysisFindingLocation.unlocated(finding_id="p23-f1"),),
        source_signature=GetAnalysisVisualization._target_signature((first,)),
    )
    assert GetAnalysisVisualization._cached_page_matches(
        cached_page=cached, targets=(first,)
    )
    assert not GetAnalysisVisualization._cached_page_matches(
        cached_page=cached, targets=(changed,)
    )


def test_previous_localization_policy_cache_is_invalidated() -> None:
    """Старое unlocated не скрывает исправленные координаты после обновления."""
    target = AnalysisFindingTarget(
        finding_id="p22-dpos-8-5-4",
        comment="Проверить повторное позиционное обозначение 8.5.4.",
        evidence="Три подписи на схеме.",
        visual_regions=(),
    )
    previous_payload = [
        {
            "finding_id": target.finding_id,
            "comment": target.comment,
            "evidence": target.evidence,
            "visual_regions": [],
        }
    ]
    previous_signature = hashlib.sha256(
        json.dumps(
            previous_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cached = AnalysisVisualizationLocationPage(
        page_number=22,
        locations=(AnalysisFindingLocation.unlocated(finding_id=target.finding_id),),
        source_signature=previous_signature,
    )

    assert not GetAnalysisVisualization._cached_page_matches(
        cached_page=cached, targets=(target,)
    )


def test_three_exact_labels_are_ambiguous_no_invented_regions() -> None:
    """Три подписи без связи с объектами оставляют локализацию неопределённой."""
    matcher = FindingAnchorMatcher()
    result = matcher.locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p22-f4",
                comment="Дублирование позиционного обозначения 8.9.3.",
                evidence="Две позиции на схеме; третья может быть повторной ссылкой.",
                visual_regions=(_region(620, 440, 50, 40, "8.9.3"),),
            ),
        ),
        text_words=(
            _word("8.9.3", 415, 222, 1),
            _word("8.9.3", 845, 453, 2),
            _word("8.9.3", 190, 14, 3),
        ),
    )[0]
    assert result.status == "unlocated"


def test_changed_visual_regions_invalidate_same_id_cache() -> None:
    """Одного совпадающего идентификатора недостаточно для reuse координат."""
    original = AnalysisFindingTarget(
        finding_id="p23-f1",
        comment="Сравнить резервуары.",
        evidence="План и схема.",
        visual_regions=(_region(200, 250, 250, 150, "Резервуар"),),
    )
    changed = AnalysisFindingTarget(
        finding_id=original.finding_id,
        comment=original.comment,
        evidence=original.evidence,
        visual_regions=(_region(250, 650, 200, 200, "Резервуар"),),
    )
    cached = AnalysisVisualizationLocationPage(
        page_number=23,
        locations=(AnalysisFindingLocation.unlocated(finding_id="p23-f1"),),
        source_signature=GetAnalysisVisualization._target_signature((original,)),
    )
    assert not GetAnalysisVisualization._cached_page_matches(
        cached_page=cached,
        targets=(changed,),
    )


def test_longer_neighbour_tag_does_not_confirm_exact_duplicate() -> None:
    """Обозначение 8.9.21 не подтверждает повторение 8.9.2."""
    result = FindingAnchorMatcher().locate(
        findings=(
            AnalysisFindingTarget(
                finding_id="p22-dpos-8-9-2",
                comment="Проверить повторное обозначение 8.9.2.",
                evidence="В тексте присутствует повторение 8.9.2.",
            ),
        ),
        text_words=(
            _word("8.9.21", 640, 430, 1),
            _word("8.9.2", 847, 348, 2),
        ),
    )[0]
    assert result.status == "unlocated"
