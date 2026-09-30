# services/experience-service/src/pdrd_experience_service/domain/catalog_repair.py

"""Дополнение старого каталога доверенными метаданными без потери ручных правок."""

from dataclasses import replace

from pdrd_experience_service.domain.catalog import Example
from pdrd_experience_service.domain.review import ReviewError


def repair_example(original: Example, incoming: Example, actor: str) -> Example:
    """Заполняет пропуски; исходный текст, существующая provenance и удаление неизменны."""
    before, after = original.source, incoming.source
    same_occurrence = (before.job_id, before.finding_id, before.approved_revision) == (
        after.job_id,
        after.finding_id,
        after.approved_revision,
    )
    if same_occurrence and (
        before.original_text != after.original_text
        or before.original_basis != after.original_basis
        or (
            before.proposed_regions
            and before.proposed_regions != after.proposed_regions
        )
    ):
        raise ReviewError("Нельзя заменить первоначальные данные VLM в каталоге.")
    legacy_bad = (
        not before.proposed_regions
        and not before.issue_regions
        and (
            before.decision.value == "rejected" and after.area_source == "vlm_original"
        )
    )
    keep_confirmation = (
        bool(before.issue_regions)
        and before.area_source == "engineer_confirmed"
        and after.area_source == "unlocated"
        and before.decision.value == "accepted"
    )
    regions_changed = (
        (same_occurrence or legacy_bad)
        and not keep_confirmation
        and not before.proposed_regions
        and (before.issue_regions != after.issue_regions)
    )
    source = replace(
        before,
        section_id=before.section_id or after.section_id,
        section_title=before.section_title or after.section_title,
        proposed_regions=before.proposed_regions or after.proposed_regions,
        display_regions=after.display_regions
        if (same_occurrence or legacy_bad) and after.display_regions is not None
        else before.display_regions,
        issue_regions=after.issue_regions if regions_changed else before.issue_regions,
        area_source=after.area_source
        if (same_occurrence or legacy_bad) and not keep_confirmation
        else before.area_source,
        confirmed_by=after.confirmed_by
        if same_occurrence and not keep_confirmation
        else before.confirmed_by,
        confirmed_at=after.confirmed_at
        if same_occurrence and not keep_confirmation
        else before.confirmed_at,
    )
    if (
        same_occurrence
        and before.decision.value == "rejected"
        and not after.proposed_regions
        and before.issue_regions
    ):
        source = replace(
            source, display_regions=after.display_regions or before.issue_regions
        )
    # Повторный прогон не меняет автора/время решения первичного источника.
    if not same_occurrence:
        source = replace(
            source, confirmed_by=before.confirmed_by, confirmed_at=before.confirmed_at
        )
    crops = (
        incoming.crops
        if regions_changed or len(original.crops) != len(source.issue_regions)
        else original.crops
    )
    result = replace(
        original,
        source=source,
        crops=crops,
        section_id=original.section_id or incoming.section_id,
        section_title=original.section_title or incoming.section_title,
    )
    if result == original:
        return original
    return replace(
        result,
        revision=original.revision + 1,
        updated_at=incoming.updated_at,
        curated_by=actor,
    )
