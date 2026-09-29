# services/experience-service/tests/integration/test_artifact_dataset_database.py

"""PostgreSQL фиксирует геометрию и crop набора отдельно от последующих правок каталога."""

import base64
import io
import json
import zipfile
from dataclasses import replace
from uuid import UUID

import pytest
from pdrd_experience_service.application.use_cases.artifacts import ManageArtifacts
from pdrd_experience_service.application.use_cases.catalog import ManageCatalog
from pdrd_experience_service.application.use_cases.export_artifact_dataset import (
    ExportArtifactDataset,
)
from pdrd_experience_service.infrastructure.crops import LocalCropStore
from pdrd_experience_service.infrastructure.database.artifact_models import (
    AppliedArtifactModel,
    ArtifactEventModel,
    ArtifactMemberModel,
    ArtifactVersionModel,
)
from pdrd_experience_service.infrastructure.database.artifacts import (
    SqlAlchemyArtifactRepository,
)
from sqlalchemy import delete

from .test_catalog_database import prepared, remove_catalog
from .test_confirmed_areas_database import engine as engine

pytestmark = pytest.mark.database
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9tEAAAAASUVORK5CYII="
)


async def test_prepared_dataset_export_uses_sql_snapshot_after_example_edit_and_delete(
    engine, tmp_path
):
    """SQL CAS принимает полный fine_tune снимок, а экспорт не читает новые поля каталога."""
    data = await prepared(engine)
    versions = SqlAlchemyArtifactRepository(data[6])
    images = LocalCropStore(tmp_path / "dataset-crops")
    crop = await images.put(PNG)
    record = replace(data[4], crops=(crop,))
    version_id = None
    try:
        await data[5].capture(
            review=data[3],
            area_versions=data[7],
            examples=(record,),
            actor="integration:1",
        )
        catalog = ManageCatalog(data[5], images)
        scoped = await catalog.update(
            example_id=record.id,
            expected_revision=0,
            fields={"section_id": "СП 1:6", "section_title": "Раздел 6"},
            actor="integration:1",
        )
        created = await ManageArtifacts(versions, data[5]).create(
            kind="fine_tune",
            name="Снимок PostgreSQL",
            model="shared-vlm",
            references=((scoped.example.id, 1),),
            actor="integration:1",
        )
        version_id = UUID(created["id"])
        loaded = await versions.get(version_id)
        assert (
            loaded.members[0]["geometry"]["issue_regions"][0]["x_min"]
            == record.source.issue_regions[0].x_min
        )
        exporter = ExportArtifactDataset(versions, images)
        frozen = await exporter.execute(version_id)
        await catalog.update(
            example_id=record.id,
            expected_revision=1,
            fields={"text": "Исправлено после создания набора"},
            actor="integration:2",
        )
        await catalog.delete_many(references=((record.id, 2),), actor="integration:2")
        assert (await data[5].get(record.id)).example.deleted
        assert await exporter.execute(version_id) == frozen
        with zipfile.ZipFile(io.BytesIO(frozen)) as archive:
            row = json.loads(archive.read("examples.jsonl"))
            assert row["text"] == scoped.example.text
            assert row["example_revision"] == 1
            assert archive.read(row["crop_files"][0]["path"]) == PNG
        assert (
            await versions.claim(
                worker="worker", model="shared-vlm", identity="a" * 16, dimension=2
            )
            is None
        )
    finally:
        if version_id is not None:
            async with data[6]() as database, database.begin():
                for model in (
                    AppliedArtifactModel,
                    ArtifactEventModel,
                    ArtifactMemberModel,
                ):
                    await database.execute(
                        delete(model).where(model.version_id == version_id)
                    )
                await database.execute(
                    delete(ArtifactVersionModel).where(
                        ArtifactVersionModel.id == version_id
                    )
                )
        await remove_catalog(engine, data[3].job_id)
