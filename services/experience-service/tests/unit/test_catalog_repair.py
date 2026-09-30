# services/experience-service/tests/unit/test_catalog_repair.py

"""Дополнение старых источников сохраняет редакции инженера и историю исходных данных."""

from dataclasses import replace

import pytest
from pdrd_experience_service.domain.catalog_identity import content_key
from pdrd_experience_service.domain.catalog_repair import repair_example
from pdrd_experience_service.domain.review import (
    Decision,
    ProposedRegion,
    Rectangle,
    ReviewError,
)

from .test_catalog import example


def test_section_backfill_preserves_curation_deletion_and_identity():
    """Автоматическое уточнение раздела не воскрешает запись и не размножает пример."""
    original = replace(example(), text="Редакция инженера", deleted=True, active=False)
    incoming = replace(
        original,
        source=replace(original.source, section_id="section", section_title="Раздел"),
        section_id="section",
        section_title="Раздел",
    )
    repaired = repair_example(original, incoming, "engineer:2")
    assert repaired.deleted and not repaired.active and repaired.text == original.text
    assert repaired.revision == original.revision + 1
    assert repaired.source.original_text == original.source.original_text
    assert content_key(original.source) == content_key(repaired.source)
    assert repair_example(repaired, incoming, "engineer:2") == repaired


def test_existing_original_regions_cannot_be_replaced():
    """После восстановления первоначальные координаты остаются неизменными."""
    original = example()
    original = replace(
        original,
        source=replace(
            original.source,
            proposed_regions=(
                ProposedRegion(original.source.issue_regions[0], "vlm", 0.9, "vlm"),
            ),
        ),
    )
    incoming = replace(
        original,
        source=replace(
            original.source,
            proposed_regions=(
                ProposedRegion(Rectangle(1, 2, 3, 4), "vlm", 0.9, "vlm"),
            ),
        ),
    )
    with pytest.raises(ReviewError, match="первоначальные"):
        repair_example(original, incoming, "engineer:2")


def test_dedup_distinguishes_engineer_region_from_original_bad():
    """Перемещение до отказа создаёт новую редакцию примера при неизменном исходном crop."""
    original = example().source
    proposal = ProposedRegion(original.issue_regions[0], "vlm", 0.9, "vlm")
    original = replace(original, proposed_regions=(proposal,))
    assert content_key(original) == content_key(
        replace(original, display_regions=original.issue_regions)
    )
    assert content_key(original) != content_key(
        replace(original, display_regions=(Rectangle(1, 2, 3, 4),))
    )


def test_bad_without_vlm_coordinates_does_not_reuse_previously_confirmed_engineer_crop():
    """Старое принятие нарисованной области не создаёт первоначальные координаты модели."""
    original = example(tag="bad", decision=Decision.REJECTED)
    incoming = replace(
        original,
        crops=(),
        source=replace(original.source, issue_regions=(), area_source="unlocated"),
    )
    repaired = repair_example(original, incoming, "engineer:2")
    assert (
        not repaired.crops
        and not repaired.source.issue_regions
        and not repaired.source.proposed_regions
    )
    assert repaired.source.display_regions == original.source.issue_regions
    assert repair_example(repaired, incoming, "engineer:2") == repaired
