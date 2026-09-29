# services/experience-service/tests/integration/test_artifact_database.py

"""Изолированный PostgreSQL: состав, аренда, CAS, допуск и аудит версий."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from pdrd_experience_service.application.use_cases.artifacts import ManageArtifacts
from pdrd_experience_service.application.use_cases.catalog import ManageCatalog
from pdrd_experience_service.domain.catalog import CatalogFilter
from pdrd_experience_service.domain.catalog_identity import content_key
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError
from pdrd_experience_service.infrastructure.database.artifact_models import (
    AppliedArtifactModel,
    ArtifactEventModel,
    ArtifactMemberModel,
    ArtifactVersionModel,
)
from pdrd_experience_service.infrastructure.database.artifacts import (
    SqlAlchemyArtifactRepository,
)
from pdrd_experience_service.infrastructure.database.catalog_models import (
    CatalogOccurrenceModel,
)
from sqlalchemy import delete, func, select, text, update

from .test_catalog_database import prepared, remove_catalog
from .test_confirmed_areas_database import engine as engine

pytestmark = pytest.mark.database
MIGRATION_CONFIG = Path(__file__).resolve().parents[2] / "alembic.ini"


async def test_upgrade_preserves_existing_catalog_and_backfills_dedup_key(engine):
    """Реальный переход 0003 → 0004 сохраняет старый JSON, crop и редакции."""
    data = await prepared(engine)
    configuration = Config(str(MIGRATION_CONFIG))
    try:
        await data[5].capture(
            review=data[3],
            area_versions=data[7],
            examples=(data[4],),
            actor="integration:1",
        )
        await asyncio.to_thread(
            alembic_command.downgrade, configuration, "20260928_0003"
        )
        await asyncio.to_thread(alembic_command.upgrade, configuration, "head")
        restored = await data[5].get(data[4].id)
        assert restored.example == data[4] and restored.active
        assert len(await data[5].history(data[4].id)) == 1
        async with engine.connect() as connection:
            assert await connection.scalar(
                text(
                    "SELECT content_key FROM experience.catalog_examples WHERE id=:id"
                ),
                {"id": data[4].id},
            ) == content_key(data[4].source)
    finally:
        await asyncio.to_thread(alembic_command.upgrade, configuration, "head")
        await remove_catalog(engine, data[3].job_id)


async def test_cross_job_dedup_preserves_both_sources_and_currentness(engine):
    """Конкурентные утверждения одинакового PDF создают один пример с двумя происхождениями."""
    first = await prepared(engine, source_sha256="d" * 64)
    second = await prepared(engine, source_sha256="d" * 64)
    try:
        results = await asyncio.gather(
            *[
                data[5].capture(
                    review=data[3],
                    area_versions=data[7],
                    examples=(data[4],),
                    actor="integration:1",
                )
                for data in (first, second)
            ]
        )
        assert sum(item["created"] for item in results) == 1
        rows, total = await first[5].list(CatalogFilter(job_id=first[3].job_id))
        assert total == 1
        example_id = rows[0].example.id
        assert (await second[5].list(CatalogFilter(job_id=second[3].job_id)))[0][
            0
        ].example.id == example_id
        async with first[6]() as database:
            assert (
                await database.scalar(
                    select(func.count())
                    .select_from(CatalogOccurrenceModel)
                    .where(CatalogOccurrenceModel.example_id == example_id)
                )
                == 2
            )
        for data in (first, second):
            await data[2].execute(
                job_id=data[3].job_id,
                finding_id="vlm:1",
                reason="Область неверна",
                actor="integration:1",
                expected_review_revision=data[3].revision,
                expected_confirmation_revision=1,
            )
            entry = await first[5].get(example_id)
            assert entry.source_current is (data is first)
    finally:
        for data in (first, second):
            await remove_catalog(engine, data[3].job_id)


async def test_bulk_delete_conflict_rolls_back_all_rows_and_audits(engine):
    """Удаление набора атомарно; tombstone исключает повторное сохранение старого примера."""
    first, second = await prepared(engine), await prepared(engine)
    try:
        for data in (first, second):
            await data[5].capture(
                review=data[3],
                area_versions=data[7],
                examples=(data[4],),
                actor="integration:1",
            )
        management = ManageCatalog(first[5], None)
        await management.update(
            example_id=second[4].id,
            expected_revision=0,
            fields={"document_title": "Новое название"},
            actor="integration:2",
        )
        with pytest.raises(ReviewConflictError):
            await management.delete_many(
                references=((first[4].id, 0), (second[4].id, 0)), actor="integration:1"
            )
        assert not (await first[5].get(first[4].id)).example.deleted
        assert len(await first[5].history(first[4].id)) == 1
        assert await management.delete_many(
            references=((first[4].id, 0), (second[4].id, 1)), actor="integration:1"
        ) == {"deleted": 2}
        for data in (first, second):
            assert (await data[5].list(CatalogFilter(job_id=data[3].job_id)))[1] == 0
            again = await data[5].capture(
                review=data[3],
                area_versions=data[7],
                examples=(data[4],),
                actor="integration:1",
            )
            assert (
                again["created"] == 0
                and (await data[5].get(data[4].id)).example.deleted
            )
    finally:
        for data in (first, second):
            await remove_catalog(engine, data[3].job_id)


async def test_version_queue_manifest_cas_lease_and_quality_gate(engine):
    """Worker не назначает качество; выбор для просмотра не меняет applied."""
    data = await prepared(engine)
    versions = SqlAlchemyArtifactRepository(data[6])
    created_ids = []
    try:
        await data[5].capture(
            review=data[3],
            area_versions=data[7],
            examples=(data[4],),
            actor="integration:1",
        )
        curated = await ManageCatalog(data[5], None).update(
            example_id=data[4].id,
            expected_revision=0,
            fields={"section_id": "СП 1:6.3", "section_title": "СП 1, раздел 6.3"},
            actor="integration:1",
        )
        manager = ManageArtifacts(versions, data[5])
        record = await manager.create(
            kind="vector",
            name="Первая версия",
            model="shared-embedding",
            references=((curated.example.id, 1),),
            actor="integration:1",
        )
        version_id = UUID(record["id"])
        created_ids.append(version_id)
        assert record["status"] == "queued" and not record["quality_approved"]
        renamed = await versions.rename(
            version_id=version_id, revision=0, name="Новое имя", actor="integration:2"
        )
        assert renamed.manifest_sha256 == record["manifest_sha256"]
        assert renamed.members[0]["example_revision"] == 1
        with pytest.raises(ReviewConflictError):
            await versions.rename(
                version_id=version_id,
                revision=0,
                name="Старая вкладка",
                actor="integration:1",
            )
        assert (
            await versions.claim(
                worker="other", model="other-model", identity="b" * 16, dimension=3
            )
            is None
        )
        claims = await asyncio.gather(
            *[
                versions.claim(
                    worker=f"worker_{index}",
                    model="shared-embedding",
                    identity="a" * 16,
                    dimension=3,
                )
                for index in range(2)
            ]
        )
        assert sum(item is not None for item in claims) == 1
        current = next(item for item in claims if item is not None)
        owner = f"worker_{claims.index(current)}"
        with pytest.raises(ReviewConflictError):
            await versions.finish(
                version_id=version_id, worker="other", result={"status": "ready"}
            )
        with pytest.raises(ReviewConflictError):
            await versions.delete(
                version_id=version_id, revision=current.revision, actor="integration:1"
            )
        # Истёкшая аренда восстанавливается новым worker; прежний не публикует результат.
        async with data[6]() as database, database.begin():
            await database.execute(
                update(ArtifactVersionModel)
                .where(ArtifactVersionModel.id == version_id)
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        recovered = await versions.claim(
            worker="replacement",
            model="shared-embedding",
            identity="a" * 16,
            dimension=3,
        )
        assert recovered.id == version_id and recovered.members == current.members
        with pytest.raises(ReviewConflictError):
            await versions.finish(
                version_id=version_id, worker=owner, result={"status": "ready"}
            )
        await versions.finish(
            version_id=version_id, worker="replacement", result={"status": "heartbeat"}
        )
        ready = await versions.finish(
            version_id=version_id, worker="replacement", result={"status": "ready"}
        )
        assert ready.status == "ready" and not ready.quality_approved
        with pytest.raises(ReviewError):
            await versions.apply(
                version_id=version_id, revision=ready.revision, actor="integration:1"
            )
        assert await versions.applied() == ()
        await versions.delete(
            version_id=version_id, revision=ready.revision, actor="integration:1"
        )
        assert await versions.list() == ()
        assert (await versions.get(version_id)).members == ready.members
        # Набор дообучения фиксируется, но не объявляется готовыми весами.
        dataset = await manager.create(
            kind="fine_tune",
            name="Набор",
            model="shared-vlm",
            references=((curated.example.id, 1),),
            actor="integration:1",
        )
        created_ids.append(UUID(dataset["id"]))
        assert dataset["status"] == "prepared" and dataset["weights_sha256"] == ""
    finally:
        async with data[6]() as database, database.begin():
            for model in (
                AppliedArtifactModel,
                ArtifactEventModel,
                ArtifactMemberModel,
            ):
                await database.execute(
                    delete(model).where(model.version_id.in_(created_ids))
                )
            await database.execute(
                delete(ArtifactVersionModel).where(
                    ArtifactVersionModel.id.in_(created_ids)
                )
            )
        await remove_catalog(engine, data[3].job_id)
