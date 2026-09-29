# services/experience-service/tests/integration/test_artifact_database.py

"""Изолированный PostgreSQL: состав, аренда, CAS, допуск и аудит версий."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from pdrd_experience_service.application.catalog_snapshot import example_to_json
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
    CatalogEventModel,
    CatalogExampleModel,
    CatalogOccurrenceModel,
)
from sqlalchemy import delete, func, select, text, update

from tests.artifact_quality_support import report_for

from .test_catalog_database import prepared, rejected_source, remove_catalog
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


async def create_ready_vector(data, versions, name="Проверка допуска"):
    """Фиксирует серверный состав и только готовность; качество ещё отсутствует."""
    entries, _ = await data[5].list(CatalogFilter(job_id=data[3].job_id))
    example = entries[0].example
    manager = ManageArtifacts(versions, data[5])
    record = await manager.create(
        kind="vector",
        name=name,
        model="shared-embedding",
        references=((example.id, example.revision),),
        actor="integration:1",
    )
    version_id = UUID(record["id"])
    await versions.claim(
        worker="quality_test", model="shared-embedding", identity="a" * 16, dimension=3
    )
    return await versions.finish(
        version_id=version_id, worker="quality_test", result={"status": "ready"}
    )


async def prepare_quality_catalog(engine):
    """Подготавливает реальный каталог с проверенной областью и разделом."""
    data = await prepared(engine)
    await data[5].capture(
        review=data[3],
        area_versions=data[7],
        examples=(data[4],),
        actor="integration:1",
    )
    await ManageCatalog(data[5], None).update(
        example_id=data[4].id,
        expected_revision=0,
        fields={"section_id": "СП 1:6.3", "section_title": "СП 1, раздел 6.3"},
        actor="integration:1",
    )
    return data


async def remove_versions(data, ids):
    """Удаляет только собственные данные из изолированной тестовой базы."""
    async with data[6]() as database, database.begin():
        for model in (
            AppliedArtifactModel,
            ArtifactEventModel,
            ArtifactMemberModel,
            ArtifactVersionModel,
        ):
            column = model.id if model is ArtifactVersionModel else model.version_id
            await database.execute(delete(model).where(column.in_(ids)))


async def test_quality_cas_and_revoke_are_atomic_and_keep_report_history(engine):
    """Конкурентные отчёты не теряют ревизии; отзыв снимает назначение и остаётся в аудите."""
    data = await prepare_quality_catalog(engine)
    versions, ids = SqlAlchemyArtifactRepository(data[6]), []
    try:
        ready = await create_ready_vector(data, versions)
        ids.append(ready.id)
        report = report_for(ready)
        results = await asyncio.gather(
            *[
                versions.record_quality(
                    version_id=ready.id,
                    revision=ready.revision,
                    report=report,
                    actor=f"integration:{actor}",
                )
                for actor in (1, 2)
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(item, ReviewConflictError) for item in results) == 1
        approved = await versions.get(ready.id)
        assert approved.quality_approved and approved.quality_report == report
        assert await versions.applied() == ()
        await versions.apply(
            version_id=ready.id, revision=approved.revision, actor="integration:1"
        )
        active = await versions.get(ready.id)
        assert (
            await ManageArtifacts(versions, data[5]).read_applied(active.section_id)
        )["item"]["id"] == str(active.id)
        with pytest.raises(ReviewConflictError):
            await versions.delete(
                version_id=ready.id, revision=active.revision, actor="integration:1"
            )
        with pytest.raises(ReviewConflictError):
            await versions.record_quality(
                version_id=ready.id,
                revision=approved.revision,
                report=None,
                actor="integration:1",
            )
        assert len(await versions.applied()) == 1
        revoked = await versions.record_quality(
            version_id=ready.id,
            revision=active.revision,
            report=None,
            actor="integration:1",
        )
        assert not revoked.quality_approved and revoked.members == ready.members
        assert await versions.applied() == ()
        async with data[6]() as database:
            events = (
                await database.scalars(
                    select(ArtifactEventModel).where(
                        ArtifactEventModel.version_id == ready.id
                    )
                )
            ).all()
            admissions = [
                event for event in events if event.operation == "quality_report"
            ]
            assert (
                len(admissions) == 1
                and admissions[0].snapshot["quality_report"] == report
            )
            assert (
                len([event for event in events if event.operation == "quality_revoke"])
                == 1
            )
        await versions.delete(
            version_id=ready.id, revision=revoked.revision, actor="integration:1"
        )
    finally:
        await remove_versions(data, ids)
        await remove_catalog(engine, data[3].job_id)


@pytest.mark.parametrize("already_applied", [False, True])
async def test_changed_catalog_blocks_quality_and_runtime_without_changing_manifest(
    engine, already_applied
):
    """Устаревшая редакция не получает новый допуск и исчезает из рабочего поиска."""
    data = await prepare_quality_catalog(engine)
    versions, ids = SqlAlchemyArtifactRepository(data[6]), []
    try:
        ready = await create_ready_vector(data, versions)
        ids.append(ready.id)
        report = report_for(ready)
        if already_applied:
            approved = await versions.record_quality(
                version_id=ready.id,
                revision=ready.revision,
                report=report,
                actor="integration:1",
            )
            await versions.apply(
                version_id=ready.id, revision=approved.revision, actor="integration:1"
            )
        current = await versions.get(ready.id)
        await ManageCatalog(data[5], None).update(
            example_id=data[4].id,
            expected_revision=1,
            fields={"text": "Исправленный каталог"},
            actor="integration:1",
        )
        with pytest.raises(ReviewConflictError):
            await versions.record_quality(
                version_id=ready.id,
                revision=current.revision,
                report=report,
                actor="integration:1",
            )
        if already_applied:
            with pytest.raises(ReviewConflictError):
                await versions.apply(
                    version_id=ready.id,
                    revision=current.revision,
                    actor="integration:1",
                )
        assert (await versions.get(ready.id)).manifest_sha256 == ready.manifest_sha256
        assert (
            await ManageArtifacts(versions, data[5]).read_applied(ready.section_id)
        )["item"] is None
    finally:
        await remove_versions(data, ids)
        await remove_catalog(engine, data[3].job_id)


async def test_replacement_and_explicit_rollback_choose_one_version_per_section(engine):
    """Откат снова назначает проверенную предыдущую коллекцию, не смешивая составы."""
    data = await prepare_quality_catalog(engine)
    versions, ids = SqlAlchemyArtifactRepository(data[6]), []
    try:
        for name in ("Первая", "Вторая"):
            ready = await create_ready_vector(data, versions, name=name)
            ids.append(ready.id)
            approved = await versions.record_quality(
                version_id=ready.id,
                revision=ready.revision,
                report=report_for(ready),
                actor="integration:1",
            )
            await versions.apply(
                version_id=ready.id, revision=approved.revision, actor="integration:1"
            )
        assert len(await versions.applied()) == 1
        assert (await versions.applied())[0]["version_id"] == str(ids[1])
        first = await versions.get(ids[0])
        await versions.apply(
            version_id=first.id, revision=first.revision, actor="integration:1"
        )
        assert (await versions.applied())[0]["version_id"] == str(ids[0])
        assert (
            await ManageArtifacts(versions, data[5]).read_applied(first.section_id)
        )["item"]["collection"] == first.collection
    finally:
        await remove_versions(data, ids)
        await remove_catalog(engine, data[3].job_id)


async def test_legacy_bad_migration_and_repeated_pdf_repair_without_duplicate(engine):
    """Старый текстовый Bad с известной VLM-областью не размножается новым прогоном."""
    first = await rejected_source(engine, sha="e" * 64)
    second = await rejected_source(engine, sha="e" * 64)
    review, example, repository = first
    configuration = Config(str(MIGRATION_CONFIG))
    try:
        await repository.capture(
            review=review, area_versions=(), examples=(example,), actor="integration:1"
        )
        legacy = replace(
            example,
            crops=(),
            source=replace(
                example.source,
                issue_regions=(),
                proposed_regions=(),
                area_source="engineer_confirmed",
            ),
        )
        legacy_json = example_to_json(legacy)
        for field in ("proposed_regions", "display_regions", "area_source"):
            legacy_json["source"].pop(field)
        async with engine.begin() as connection:
            await connection.execute(
                update(CatalogExampleModel)
                .where(CatalogExampleModel.id == example.id)
                .values(snapshot=legacy_json, content_key=content_key(legacy.source))
            )
            await connection.execute(
                update(CatalogEventModel)
                .where(CatalogEventModel.example_id == example.id)
                .values(snapshot=legacy_json)
            )
        await asyncio.to_thread(
            alembic_command.downgrade, configuration, "20260929_0004"
        )
        await asyncio.to_thread(alembic_command.upgrade, configuration, "head")
        existing = await repository.find_source(second[1].source)
        assert existing.example.id == example.id and existing.example.crops == ()
        incoming = replace(
            second[1],
            id=example.id,
            section_id="section",
            section_title="Раздел",
            source=replace(
                second[1].source, section_id="section", section_title="Раздел"
            ),
        )
        result = await repository.capture(
            review=second[0],
            area_versions=(),
            examples=(incoming,),
            actor="integration:1",
            expected_revisions=((example.id, 0),),
        )
        assert result["created"] == 0 and result["repaired"][0]["revision"] == 1
        repaired = (await repository.get(example.id)).example
        assert (
            repaired.crops
            and repaired.source.proposed_regions == review.findings[0].proposed_regions
        )
        assert (
            repaired.source.job_id == review.job_id
            and repaired.source.original_text == example.source.original_text
        )
        assert len(await repository.history(example.id)) == 2
        assert (await repository.list(CatalogFilter(job_id=second[0].job_id)))[1] == 1
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(CatalogOccurrenceModel)
                    .where(CatalogOccurrenceModel.example_id == example.id)
                )
                == 2
            )
        repeated = await repository.capture(
            review=second[0],
            area_versions=(),
            examples=(incoming,),
            actor="integration:1",
        )
        assert repeated["created"] == 0 and repeated["repaired"] == []
        with pytest.raises(ReviewConflictError):
            await repository.capture(
                review=second[0],
                area_versions=(),
                examples=(incoming,),
                actor="integration:1",
                expected_revisions=((example.id, 0),),
            )
        assert len(await repository.history(example.id)) == 2
    finally:
        await asyncio.to_thread(alembic_command.upgrade, configuration, "head")
        for data in (first, second):
            await remove_catalog(engine, data[0].job_id)


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
