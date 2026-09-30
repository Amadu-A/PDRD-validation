# services/experience-service/tests/integration/test_catalog_database.py

"""Настоящий PostgreSQL: каталог, идемпотентность, CAS, отзыв источника и миграция.

Унаследованный fixture проверяет точные host/database/user временной БД
до любых записей. Рабочая БД и её volumes недоступны этому прогону.
"""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pdrd_experience_service.application.catalog_snapshot import example_to_json
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.application.use_cases.index_feed import ReadIndexFeed
from pdrd_experience_service.domain.area_confirmation import ConfirmationMode
from pdrd_experience_service.domain.catalog import CatalogFilter, Crop, Example
from pdrd_experience_service.domain.experience_selection import (
    select_experience_candidates,
)
from pdrd_experience_service.domain.review import (
    Decision,
    ProposedRegion,
    Rectangle,
    ReviewConflictError,
)
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


async def rejected_source(engine, *, scenario="original", sha=None):
    """Утверждённый Bad без когда-либо записанного подтверждения области."""
    review = initial(uuid4())
    review = replace(
        review, source_sha256=sha or hashlib.sha256(review.job_id.bytes).hexdigest()
    )
    if scenario == "unlocated":
        review = replace(
            review, findings=(replace(review.findings[0], proposed_regions=()),)
        )
    elif scenario == "multiple":
        review = replace(
            review,
            findings=(
                replace(
                    review.findings[0],
                    proposed_regions=(
                        *review.findings[0].proposed_regions,
                        ProposedRegion(
                            Rectangle(780, 100, 980, 400),
                            "analysis_vlm",
                            0.9,
                            "analysis_vlm",
                        ),
                    ),
                ),
            ),
        )
    reviews, areas = adapters(engine)
    await reviews.insert(review)
    if scenario == "moved":
        moved = review.change_geometry(
            finding_id="vlm:1",
            regions=(replace(BOX, x_min=125),),
            callout_box=None,
            actor="integration:1",
            at=datetime.now(UTC),
            expected_revision=review.revision,
        )
        await reviews.update(moved, expected_revision=review.revision)
        review = moved
    rejected = review.decide(
        finding_id="vlm:1",
        decision=Decision.REJECTED,
        actor="integration:1",
        at=datetime.now(UTC),
        expected_revision=review.revision,
    )
    await reviews.update(rejected, expected_revision=review.revision)
    approved = rejected.approve(
        actor="integration:1", at=datetime.now(UTC), expected_revision=rejected.revision
    )
    await reviews.update(approved, expected_revision=rejected.revision)
    source = select_experience_candidates(
        session=approved, confirmed_areas=(), for_catalog=True
    )[0]
    at = datetime.now(UTC)
    example = Example(
        uuid4(),
        source,
        tuple(
            Crop(hashlib.sha256(str(index).encode()).hexdigest(), 200, 100)
            for index in range(len(source.issue_regions))
        ),
        0,
        "План",
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
    repository = SqlAlchemyCatalogRepository(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    assert await areas.load_status(review=approved) == ()
    return approved, example, repository


@pytest.mark.parametrize("scenario", ["original", "moved", "unlocated", "multiple"])
async def test_bad_without_confirmation_persists_original_and_modified_regions(
    engine, scenario
):
    """PostgreSQL не требует подтверждения первоначальной области Bad, текстовый Bad остаётся в каталоге."""
    review, example, catalog = await rejected_source(engine, scenario=scenario)
    try:
        result = await catalog.capture(
            review=review, area_versions=(), examples=(example,), actor="integration:1"
        )
        assert result["created"] == 1
        record = await catalog.get(example.id)
        assert record.active and record.example.source.confirmed_by == ""
        assert (
            record.example.source.proposed_regions
            == review.findings[0].proposed_regions
        )
        assert (
            record.example.source.display_regions == review.findings[0].display_regions
        )
        assert bool(record.example.crops) == (scenario != "unlocated")
        assert len(record.example.crops) == len(record.example.source.issue_regions)
        if scenario != "unlocated":
            assert record.example.source.issue_regions == tuple(
                item.bbox for item in review.findings[0].proposed_regions
            )
        repeated = await catalog.capture(
            review=review, area_versions=(), examples=(example,), actor="integration:1"
        )
        assert repeated["created"] == 0 and repeated["repaired"] == []
        assert len(await catalog.history(example.id)) == 1
    finally:
        await remove_catalog(engine, review.job_id)


async def prepared(engine, *, source_sha256=None):
    """Создаёт и утверждает один VLM-пример через настоящие Review и Area repository."""
    job_id = uuid4()
    review = replace(
        initial(job_id),
        source_sha256=source_sha256 or hashlib.sha256(job_id.bytes).hexdigest(),
    )
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
                == "20260929_0005"
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


@pytest.mark.parametrize("exclude", ["inactive", "revoked"])
async def test_index_keyset_scan_keeps_cursor_across_excluded_examples(engine, exclude):
    """Настоящие UUID/SQL JOIN: исключённая первая строка не скрывает следующую страницу E."""
    fixtures = [await prepared(engine) for _ in range(2)]
    try:
        for _, _, _, approved, example, catalog, _, versions in fixtures:
            await catalog.capture(
                review=approved,
                area_versions=versions,
                examples=(example,),
                actor="integration:1",
            )
        fixtures.sort(key=lambda item: item[4].id)
        _, _, revoke, approved, example, catalog, _, _ = fixtures[0]
        if exclude == "inactive":
            await catalog.update(
                example=example.curate(
                    fields={"active": False},
                    actor="integration:2",
                    at=datetime.now(UTC),
                ),
                expected_revision=0,
            )
        else:
            await revoke.execute(
                job_id=approved.job_id,
                finding_id="vlm:1",
                actor="integration:2",
                reason="Отозвана область",
                expected_review_revision=approved.revision,
                expected_confirmation_revision=1,
            )
            candidate = select_experience_candidates(
                session=approved, confirmed_areas=(), for_catalog=True
            )[0]
            incoming = replace(example, source=candidate, crops=())
            versions = tuple(
                (item.finding_id, item.revision)
                for item in await fixtures[0][1].load_status(review=approved)
            )
            result = await catalog.capture(
                review=approved,
                area_versions=versions,
                examples=(incoming,),
                actor="integration:1",
            )
            assert result["created"] == 0
            assert not (await catalog.get(example.id)).source_current
        raw = await catalog.scan(after=None, limit=1)
        assert raw[0].example.id == example.id and not raw[0].active
        feed = ReadIndexFeed(catalog, None)
        page = await feed.page(limit=1)
        assert page == {"items": [], "next_after": str(example.id)}
        following = await feed.page(after=example.id, limit=1)
        assert following["items"][0]["example_id"] == str(fixtures[1][4].id)
        references = tuple(
            {
                key: item[key]
                for key in ("example_id", "example_revision", "fingerprint")
            }
            for item in following["items"]
        )
        assert len(await feed.verify(references)) == 1
        batch = await catalog.get_many((example.id, fixtures[1][4].id, uuid4()))
        assert len(batch) == 2 and sum(entry.active for entry in batch) == 1
        assert (await feed.page(after=fixtures[1][4].id, limit=1))["next_after"] is None
        assert len(await catalog.history(example.id)) == (
            2 if exclude == "inactive" else 1
        )
    finally:
        for item in fixtures:
            await remove_catalog(engine, item[3].job_id)
