# services/experience-service/src/pdrd_experience_service/application/catalog_snapshot.py

"""Нейтральный снимок источника и редакции для хранения, аудита и экспорта.

Не зависит от SQL/HTTP и не открывает файлы или соединения.
"""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from pdrd_experience_service.domain.catalog import Crop, Example
from pdrd_experience_service.domain.experience_selection import (
    ExperienceCandidate,
    LearningUse,
)
from pdrd_experience_service.domain.review import (
    Decision,
    Origin,
    ProposedRegion,
    Rectangle,
)


def encode(value):
    """Сохраняет UUID/время явно, не используя pickle или динамические классы."""
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    return value


def example_to_json(example: Example) -> dict:
    """Источник и инженерная редакция сохраняются в одном атомарном снимке."""
    return encode(asdict(example))


def example_from_json(value: dict) -> Example:
    """Восстанавливает фиксированные доменные типы и исходные координаты."""
    fields = dict(value)
    source = dict(fields.pop("source"))
    for name in ("job_id", "document_id"):
        source[name] = UUID(source[name])
    source["origin"] = Origin(source["origin"])
    source["decision"] = Decision(source["decision"])
    source["learning_use"] = LearningUse(source["learning_use"])
    source["confirmed_at"] = datetime.fromisoformat(source["confirmed_at"])
    source["issue_regions"] = tuple(Rectangle(**box) for box in source["issue_regions"])
    source["proposed_regions"] = tuple(
        ProposedRegion(**{**region, "bbox": Rectangle(**region["bbox"])})
        for region in source.get("proposed_regions", [])
    )
    if source.get("display_regions") is not None:
        source["display_regions"] = tuple(
            Rectangle(**box) for box in source["display_regions"]
        )
    if source["callout_box"] is not None:
        source["callout_box"] = Rectangle(**source["callout_box"])
    fields["id"] = UUID(fields["id"])
    fields["created_at"] = datetime.fromisoformat(fields["created_at"])
    fields["updated_at"] = datetime.fromisoformat(fields["updated_at"])
    fields["crops"] = tuple(Crop(**item) for item in fields["crops"])
    return Example(source=ExperienceCandidate(**source), **fields)
