# services/experience-service/src/pdrd_experience_service/domain/artifact_projection.py

"""Фиксирует состав версии, сохраняя прежний контракт векторной индексации.

Для подготовки дообучения дополнительно сохраняет исходную и инженерную
геометрию. Старые версии не дополняются данными изменившегося каталога.
"""

from dataclasses import asdict

from pdrd_experience_service.domain.catalog import CatalogEntry
from pdrd_experience_service.domain.index_projection import index_projection


def artifact_member_projection(entry: CatalogEntry, *, kind: str) -> dict | None:
    """Оставляет fingerprint E прежним, а обучающий снимок делает полным."""
    projection = index_projection(entry)
    if projection is None or kind != "fine_tune":
        return projection
    source = entry.example.source
    return {
        **projection,
        "dataset_snapshot_version": 1,
        "origin": source.origin.value,
        "original_basis": source.original_basis,
        "source_normative_basis": source.normative_basis,
        "created_by": source.created_by,
        "updated_by": source.updated_by,
        "geometry": {
            "coordinate_system": "normalized_0_1000",
            "proposed_regions": [asdict(item) for item in source.proposed_regions],
            "issue_regions": [asdict(box) for box in source.issue_regions],
            "display_regions": (
                [asdict(box) for box in source.display_regions]
                if source.display_regions is not None
                else None
            ),
            "callout_box": asdict(source.callout_box)
            if source.callout_box is not None
            else None,
            "area_source": source.area_source,
        },
    }
