# services/experience-service/tests/integration/test_catalog_database.py

"""Настоящий PostgreSQL: каталог, идемпотентность, CAS, отзыв источника и миграция.

Унаследованный fixture проверяет точные host/database/user временной БД
до любых записей. Рабочая БД и её volumes недоступны этому прогону.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pdrd_experience_service.application.catalog_snapshot import example_to_json
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.domain.area_confirmation import ConfirmationMode
from pdrd_experience_service.domain.catalog import CatalogFilter, Crop, Example
from pdrd_experience_service.domain.experience_selection import (
    select_experience_candidates,
)
from pdrd_experience_service.domain.review import ReviewConflictError
from pdrd_experience_service.infrastructure.database.catalog import (
    SqlAlchemyCatalogRepository,
)
from pdrd_experience_service.infrastructure.database.catalog_models import (
    CatalogExampleModel,
)
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from .test_confirmed_areas_database import BOX, adapters, cleanup, initial, seal
from .test_confirmed_areas_database import engine as engine

pytestmark = pytest.mark.database


async def prepared(engine):
    """Создаёт и утверждает один VLM-пример через настоящие Review и Area repository."""
    job_id = uuid4()
    review = initial(job_id)
    reviews, areas = adapters(engine)
    confirm = ConfirmArea(reviews, areas)
    revoke = RevokeArea(reviews, areas)
    await reviews.insert(review)
    await confirm.execute(
        job_id=job_id,
        finding_id="vlm:1",
        regions=(BOX,),
        mode=ConfirmationMode.PROPOSED,
        note="",
        actor="integration:1",
        expected_review_revision=0,
        expected_confirmation_revision=0,
    )
    approved = await seal(reviews, review)
    source = select_experience_candidates(
        session=approved, confirmed_areas=await areas.load_confirmed(job_id=job_id)
    )[0]
    at = datetime.now(UTC)
    example = Example(
        uuid4(),
        source,
        (Crop("b" * 64, 200, 100),),
        0,
        "Интеграционный план",
        source.text,
        source.normative_basis,
        "",
        True,
        "",
        "",
        at,
        at,
        "integration:1",
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    catalog = SqlAlchemyCatalogRepository(sessions)
    statuses = await areas.load_status(review=approved)
    return (
        reviews,
        areas,
        revoke,
        approved,
        example,
        catalog,
        sessions,
        tuple((item.finding_id, item.revision) for item in statuses),
    )


async def remove_catalog(engine, job_id):
    """Удаляет только записи своего случайного задания в изолированной тестовой БД."""
    async with async_sessionmaker(engine)() as database, database.begin():
        await database.execute(
            delete(CatalogExampleModel).where(CatalogExampleModel.job_id == job_id)
        )
    await cleanup(engine, job_id)


async def test_catalog_capture_is_idempotent_and_survives_repository_recreation(engine):
    """Две конкурентные вставки создают один пример/одну начальную редакцию."""
    (
        _reviews,
        _areas,
        _revoke,
        approved,
        example,
        catalog,
        sessions,
        versions,
    ) = await prepared(engine)
    try:
        results = await asyncio.gather(
            *(
                catalog.capture(
                    review=approved,
                    area_versions=versions,
                    examples=(example,),
                    actor="integration:1",
                )
                for _ in range(2)
            )
        )
        assert sum(result["created"] for result in results) == 1
        restarted = SqlAlchemyCatalogRepository(sessions)
        record = await restarted.get(example.id)
        assert record.example == example and record.active and record.source_current
        history = await restarted.history(example.id)
        assert len(history) == 1 and history[0]["snapshot"] == example_to_json(example)
        records, total = await restarted.list(
            CatalogFilter(query="ЗАМЕЧАНИЕ", job_id=approved.job_id)
        )
        assert total == 1 and records[0].example.id == example.id
        assert (await restarted.list(CatalogFilter(query="%", job_id=approved.job_id)))[
            1
        ] == 0
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT version_num FROM experience.alembic_version_experience"
                    )
                )
                == "20260928_0003"
            )
    finally:
        await remove_catalog(engine, approved.job_id)


async def test_catalog_cas_records_one_revision_and_audit_atomically(engine):
    """Одна устаревшая вкладка получает конфликт; source/crop и старая редакция сохранены."""
    (
        _reviews,
        _areas,
        _revoke,
        approved,
        example,
        catalog,
        _sessions,
        versions,
    ) = await prepared(engine)
    try:
        await catalog.capture(
            review=approved,
            area_versions=versions,
            examples=(example,),
            actor="integration:1",
        )
        edits = [
            example.curate(fields={"text": text}, actor=actor, at=datetime.now(UTC))
            for text, actor in [
                ("Первая редакция", "integration:2"),
                ("Вторая редакция", "integration:3"),
            ]
        ]
        outcomes = await asyncio.gather(
            *(catalog.update(example=item, expected_revision=0) for item in edits),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ReviewConflictError) for result in outcomes) == 1
        entry = await catalog.get(example.id)
        assert entry.example.revision == 1 and entry.example.tag == "edited"
        assert (
            entry.example.source == example.source
            and entry.example.crops == example.crops
        )
        history = await catalog.history(example.id)
        assert [event["revision"] for event in history] == [0, 1]
        assert history[0]["snapshot"]["text"] == example.text
        assert history[1]["snapshot"]["text"] == entry.example.text
        await catalog.update(
            example=entry.example.curate(
                fields={"active": False}, actor="integration:2", at=datetime.now(UTC)
            ),
            expected_revision=1,
        )
        assert (await catalog.list(CatalogFilter(active=True, job_id=approved.job_id)))[
            1
        ] == 0
        assert (
            await catalog.list(CatalogFilter(active=False, job_id=approved.job_id))
        )[1] == 1
    finally:
        await remove_catalog(engine, approved.job_id)


async def test_revocation_invalidates_catalog_and_stale_capture_under_transaction(
    engine,
):
    """Отзыв области без изменения Review немедленно закрывает обучение и запись старого crop."""
    (
        _reviews,
        _areas,
        revoke,
        approved,
        example,
        catalog,
        _sessions,
        versions,
    ) = await prepared(engine)
    try:
        await catalog.capture(
            review=approved,
            area_versions=versions,
            examples=(example,),
            actor="integration:1",
        )
        await revoke.execute(
            job_id=approved.job_id,
            finding_id="vlm:1",
            actor="integration:2",
            reason="Область не соответствует замечанию",
            expected_review_revision=approved.revision,
            expected_confirmation_revision=1,
        )
        entry = await catalog.get(example.id)
        assert not entry.source_current and not entry.active
        assert entry.example == example
        with pytest.raises(ReviewConflictError):
            await catalog.capture(
                review=approved,
                area_versions=versions,
                examples=(replace(example, id=uuid4()),),
                actor="integration:3",
            )
        assert (await catalog.list(CatalogFilter(job_id=approved.job_id)))[1] == 1
        assert len(await catalog.history(example.id)) == 1
    finally:
        await remove_catalog(engine, approved.job_id)


async def test_review_edit_invalidates_examples_without_deleting_history(engine):
    """Правки Review не переписывают ранее сохранённый обучающий материал."""
    (
        reviews,
        _areas,
        _revoke,
        approved,
        example,
        catalog,
        _sessions,
        versions,
    ) = await prepared(engine)
    try:
        await catalog.capture(
            review=approved,
            area_versions=versions,
            examples=(example,),
            actor="integration:1",
        )
        updated = approved.edit(
            finding_id="vlm:1",
            text="Исправленный Review",
            normative_basis="СП 1",
            actor="integration:2",
            at=datetime.now(UTC),
            expected_revision=approved.revision,
        )
        await reviews.update(updated, expected_revision=approved.revision)
        entry = await catalog.get(example.id)
        assert not entry.active and not entry.source_current
        assert entry.example.source.text == example.source.text
        with pytest.raises(ReviewConflictError):
            await catalog.capture(
                review=approved,
                area_versions=versions,
                examples=(),
                actor="integration:1",
            )
    finally:
        await remove_catalog(engine, approved.job_id)
