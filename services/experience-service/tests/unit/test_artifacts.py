# services/experience-service/tests/unit/test_artifacts.py

"""Состав версии зависит от раздела нормативки и допуска каждой редакции."""

from dataclasses import replace
from uuid import uuid4

import pytest
from pdrd_experience_service.application.use_cases.artifacts import ManageArtifacts
from pdrd_experience_service.domain.catalog import CatalogEntry
from pdrd_experience_service.domain.review import (
    Decision,
    ReviewConflictError,
    ReviewError,
)

from .test_catalog import AT, example


class Catalog:
    """Только порт выбранных записей; серверные HTTP и SQL проверяются отдельно."""

    def __init__(self, entries):
        """Получает серверные редакции тестового каталога."""
        self.entries = tuple(entries)

    async def get_many(self, ids):
        """Отбирает только запрошенные идентификаторы."""
        return tuple(item for item in self.entries if item.example.id in ids)


class Registry:
    """Запись наблюдается после всех проверок, до этого список остаётся пустым."""

    def __init__(self):
        """Начинает с пустого реестра."""
        self.saved = []

    async def create(self, version):
        """Фиксирует полностью проверенную версию."""
        self.saved.append(version)


def scoped():
    """Раздел нормативного документа назначен инженером, а не выведен из имени проекта."""
    return example().curate(
        fields={"section_id": "СП 1:6", "section_title": "СП 1, раздел 6"},
        actor="engineer:1",
        at=AT,
    )


@pytest.mark.parametrize(
    "kind,status", [("vector", "queued"), ("fine_tune", "prepared")]
)
async def test_manual_version_freezes_exact_revision_and_preserves_section(
    kind, status
):
    """Оба вида сохраняют один и тот же нормативный scope и точную редакцию."""
    record = scoped()
    registry = Registry()
    manager = ManageArtifacts(registry, Catalog([CatalogEntry(record, True)]))
    result = await manager.create(
        kind=kind,
        name="Версия",
        model="model",
        references=((record.id, record.revision),),
        actor="engineer:1",
    )
    assert len(registry.saved) == 1 and result["status"] == status
    assert result["section_id"] == "СП 1:6" and len(result["manifest_sha256"]) == 64
    assert result["members"][0]["example_revision"] == record.revision
    assert not result["quality_approved"] and not result["weights_sha256"]


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_section",
        "missing_crop",
        "stale",
        "deleted",
        "ambiguous",
        "mixed_sections",
        "revision",
    ],
)
async def test_invalid_selection_never_writes_partial_version(invalid):
    """Любая непригодная строка отклоняет весь набор до записи."""
    record = scoped()
    current = True
    records = []
    if invalid == "missing_section":
        record = example()
    if invalid == "missing_crop":
        record = replace(record, crops=())
    if invalid == "stale":
        current = False
    if invalid == "deleted":
        record = replace(record, deleted=True)
    if invalid == "ambiguous":
        record = replace(
            record,
            source=replace(record.source, tag="edited", decision=Decision.REJECTED),
        )
    records.append(CatalogEntry(record, current))
    if invalid == "mixed_sections":
        records.append(
            CatalogEntry(
                replace(
                    record, id=uuid4(), section_id="СП 2:7", section_title="Раздел 7"
                ),
                True,
            )
        )
    registry = Registry()
    manager = ManageArtifacts(registry, Catalog(records))
    references = tuple(
        (item.example.id, item.example.revision + (invalid == "revision"))
        for item in records
    )
    with pytest.raises((ReviewError, ReviewConflictError)):
        await manager.create(
            kind="vector",
            name="Версия",
            model="model",
            references=references,
            actor="engineer:1",
        )
    assert registry.saved == []


async def test_manual_selection_freezes_eligible_members_and_explains_exclusion():
    """Текстовая запись остаётся в каталоге; версия создаётся из пригодной части ручного выбора."""
    good = scoped()
    text_only = replace(
        good, id=uuid4(), crops=(), source=replace(good.source, issue_regions=())
    )
    registry = Registry()
    manager = ManageArtifacts(
        registry, Catalog([CatalogEntry(good, True), CatalogEntry(text_only, True)])
    )
    result = await manager.create(
        kind="vector",
        name="Версия",
        model="model",
        references=((good.id, good.revision), (text_only.id, text_only.revision)),
        actor="engineer:1",
    )
    assert len(result["members"]) == 1 and result["members"][0]["example_id"] == str(
        good.id
    )
    assert result["excluded"] == [
        {
            "id": str(text_only.id),
            "reason": "Нет пригодной исходной или принятой области; запись остаётся текстовой.",
        }
    ]
