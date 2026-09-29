# services/experience-service/tests/integration/test_confirmed_areas_database.py

"""Проверяет реальное хранение, отзыв и конкуренцию подтверждений PostgreSQL.

Назначение файла:
- проверять сохранение явно подтверждённых областей;
- убеждаться, что изменение текста делает старые области недействительными;
- проверять исправление, отзыв и неизменность журнала аудита;
- испытывать конкурентные операции нескольких пользователей;
- защищать рабочую базу от случайного запуска интеграционных тестов.

Используется исключительно изолированный PostgreSQL.

При чтении через AsyncConnection явно выбираем необходимые столбцы.
В отличие от AsyncSession, AsyncConnection не создаёт ORM-объекты
из выражения select(ORMModel).
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.application.use_cases.review import ChangeReview
from pdrd_experience_service.domain.area_confirmation import (
    ConfirmationMode,
)
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    ProposedRegion,
    Rectangle,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.confirmed_areas import (
    SqlAlchemyConfirmedAreasRepository,
)
from pdrd_experience_service.infrastructure.database.health import (
    DatabaseReadinessProbe,
)
from pdrd_experience_service.infrastructure.database.models import (
    AreaConfirmationEventModel,
    ConfirmedAreaModel,
    ReviewEventModel,
    ReviewSessionModel,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)
from pdrd_experience_service.infrastructure.database.review_confirmation import (
    SqlAlchemyReviewConfirmationCommitter,
)
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = pytest.mark.database

BOX = Rectangle(100, 150, 300, 350)
FIXED = Rectangle(400, 250, 500, 375)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Проверяет идентичность временной базы до первого запроса с записью."""
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Для интеграции необходим изолированный тестовый PostgreSQL.")

    raw = os.environ.get(
        "EXPERIENCE_SERVICE_TEST_DATABASE_URL",
        "",
    )

    if not raw or raw != os.environ.get("EXPERIENCE_SERVICE_DATABASE_URL"):
        pytest.skip("Не заданы согласованные URL миграции и тестирования.")

    url = make_url(raw)

    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != "experience-test-postgres"
        or url.database != "pdrd_experience_test"
        or url.username != "experience_test"
        or url.port != 5432
    ):
        raise ValueError("Использование рабочей БД в интеграционных тестах запрещено.")

    instance = create_async_engine(url)

    try:
        async with instance.connect() as connection:
            identity = (
                await connection.execute(
                    text("SELECT current_database(), current_user")
                )
            ).one()

            assert identity == (
                "pdrd_experience_test",
                "experience_test",
            )

            revision = await connection.scalar(
                text("SELECT version_num FROM experience.alembic_version_experience")
            )

            assert revision == "20260929_0004"

        assert await DatabaseReadinessProbe(
            instance,
            timeout_seconds=5,
        ).is_ready()

        yield instance

    finally:
        await instance.dispose()


def initial(job_id: UUID) -> ReviewSession:
    """Создаёт исходный Review с одной неподтверждённой VLM-областью."""
    return ReviewSession.open(
        job_id=job_id,
        document_id=uuid4(),
        source_filename="source.pdf",
        source_sha256="a" * 64,
        originals=(
            OriginalFinding(
                "vlm:1",
                1,
                "Замечание VLM",
                "СП 1",
                (
                    ProposedRegion(
                        BOX,
                        "analysis_vlm",
                        0.87,
                        "analysis_vlm",
                    ),
                ),
            ),
        ),
        rendered_pages=(1,),
        actor="integration:1",
        at=datetime.now(UTC),
    )


def adapters(engine: AsyncEngine):
    """Инъецирует разные AsyncSession для конкурирующих операций."""
    sessions = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    reviews = SqlAlchemyReviewRepository(
        sessions,
    )

    areas = SqlAlchemyConfirmedAreasRepository(
        sessions,
        reviews,
    )

    return reviews, areas


async def cleanup(
    engine: AsyncEngine,
    job_id: UUID,
) -> None:
    """Удаляет только тестовый job; связанные события удаляет внешний ключ."""
    async with engine.begin() as connection:
        await connection.execute(
            delete(ReviewSessionModel).where(ReviewSessionModel.job_id == job_id)
        )


def review_changes(engine, reviews, areas) -> ChangeReview:
    """Собирает production use case с настоящим атомарным SQL-портом."""
    return ChangeReview(
        reviews,
        SqlAlchemyReviewConfirmationCommitter(
            async_sessionmaker(engine, expire_on_commit=False), areas
        ),
    )


async def test_accept_current_geometry_and_idempotent_audit(engine):
    """Одна галочка сохраняет исправленную область как Wise и не дублирует аудит."""
    job = uuid4()
    reviews, areas = adapters(engine)
    changes = review_changes(engine, reviews, areas)
    try:
        await reviews.insert(initial(job))
        changed = await changes.change_geometry(
            job_id=job,
            finding_id="vlm:1",
            regions=(FIXED,),
            callout_box=None,
            actor="integration:1",
            expected_revision=0,
        )
        assert not await areas.load_status(review=changed)
        accepted = await changes.decide(
            job_id=job,
            finding_id="vlm:1",
            decision=Decision.ACCEPTED,
            actor="integration:1",
            expected_revision=changed.revision,
        )
        assert accepted.findings[0].experience_tag == "wise"
        assert accepted.findings[0].proposed_regions[0].bbox == BOX
        assert (await reviews.load(job)) == accepted
        (status,) = await areas.load_status(review=accepted)
        assert status.valid and status.regions == (FIXED,) and status.revision == 1
        repeated = await changes.decide(
            job_id=job,
            finding_id="vlm:1",
            decision=Decision.ACCEPTED,
            actor="integration:2",
            expected_revision=accepted.revision,
        )
        assert repeated == accepted
        approved = await changes.approve(
            job_id=job, actor="integration:2", expected_revision=repeated.revision
        )
        (confirmed,) = await areas.load_confirmed(job_id=job)
        assert (
            confirmed.regions == (FIXED,) and confirmed.confirmed_by == "integration:1"
        )
        async with engine.connect() as connection:
            events = (
                await connection.execute(
                    select(
                        AreaConfirmationEventModel.revision,
                        AreaConfirmationEventModel.details,
                    ).where(AreaConfirmationEventModel.job_id == job)
                )
            ).all()
        assert len(events) == 1 and events[0].revision == 1
        assert events[0].details["after"]["mode"] == "decision"
        assert approved.approved_revision == approved.revision
    finally:
        await cleanup(engine, job)


@pytest.mark.parametrize(
    "earlier_accept,resize", [(False, False), (True, False), (True, True)]
)
async def test_reject_never_confirms_new_or_changed_area(
    engine, earlier_accept, resize
):
    """Bad имеет область только после прежнего принятия без последующего изменения."""
    job = uuid4()
    reviews, areas = adapters(engine)
    changes = review_changes(engine, reviews, areas)
    try:
        opened = initial(job)
        await reviews.insert(opened)
        current = opened
        if earlier_accept:
            current = await changes.decide(
                job_id=job,
                finding_id="vlm:1",
                decision=Decision.ACCEPTED,
                actor="integration:1",
                expected_revision=current.revision,
            )
        if resize:
            current = await changes.change_geometry(
                job_id=job,
                finding_id="vlm:1",
                regions=(FIXED,),
                callout_box=None,
                actor="integration:1",
                expected_revision=current.revision,
            )
        rejected = await changes.decide(
            job_id=job,
            finding_id="vlm:1",
            decision=Decision.REJECTED,
            actor="integration:2",
            expected_revision=current.revision,
        )
        await changes.approve(
            job_id=job, actor="integration:2", expected_revision=rejected.revision
        )
        confirmed = await areas.load_confirmed(job_id=job)
        assert bool(confirmed) is (earlier_accept and not resize)
        assert rejected.findings[0].experience_tag == "bad"
        async with engine.connect() as connection:
            revisions = (
                await connection.scalars(
                    select(AreaConfirmationEventModel.revision).where(
                        AreaConfirmationEventModel.job_id == job
                    )
                )
            ).all()
        assert revisions == ([1] if earlier_accept else [])
    finally:
        await cleanup(engine, job)


async def test_failure_after_area_insert_rolls_back_decision_and_both_audits(
    engine, monkeypatch
):
    """Сбой после flush области не оставляет принятого Review либо частичного аудита."""
    job = uuid4()
    reviews, areas = adapters(engine)
    changes = review_changes(engine, reviews, areas)
    original_write = areas.write_confirmation

    async def failing_write(*args, **kwargs):
        """Вносит настоящий SQL, затем имитирует сбой до commit."""
        await original_write(*args, **kwargs)
        raise RuntimeError("test: failure after area flush")

    monkeypatch.setattr(areas, "write_confirmation", failing_write)
    opened = initial(job)
    try:
        await reviews.insert(opened)
        with pytest.raises(RuntimeError, match="after area flush"):
            await changes.decide(
                job_id=job,
                finding_id="vlm:1",
                decision=Decision.ACCEPTED,
                actor="integration:1",
                expected_revision=0,
            )
        assert await reviews.load(job) == opened
        async with engine.connect() as connection:
            for model, revision in (
                (ReviewEventModel, ReviewEventModel.session_revision),
                (ConfirmedAreaModel, ConfirmedAreaModel.revision),
                (AreaConfirmationEventModel, AreaConfirmationEventModel.revision),
            ):
                revisions = (
                    await connection.scalars(
                        select(revision).where(model.job_id == job)
                    )
                ).all()
                assert revisions == ([0] if model is ReviewEventModel else [])
    finally:
        await cleanup(engine, job)


@pytest.mark.parametrize(
    "fail,already_approved",
    [(False, False), (False, True), (True, False), (True, True)],
)
async def test_approval_confirms_legacy_accepted_review_atomically(
    engine, monkeypatch, fail, already_approved
):
    """Ранее принятое Review получает область при утверждении; сбой не утверждает его."""
    job = uuid4()
    reviews, areas = adapters(engine)
    changes = review_changes(engine, reviews, areas)
    opened = initial(job)
    accepted = opened.decide(
        finding_id="vlm:1",
        decision=Decision.ACCEPTED,
        actor="integration:1",
        at=datetime.now(UTC),
        expected_revision=0,
    )
    try:
        await reviews.insert(opened)
        await reviews.update(accepted, expected_revision=0)
        if already_approved:
            approved = accepted.approve(
                actor="integration:1",
                at=datetime.now(UTC),
                expected_revision=accepted.revision,
            )
            await reviews.update(approved, expected_revision=accepted.revision)
            accepted = approved
        if fail:
            original_write = areas.write_confirmation

            async def failing_write(*args, **kwargs):
                """Проверяет общий rollback даже после настоящей записи области."""
                await original_write(*args, **kwargs)
                raise RuntimeError("test: approval failure")

            monkeypatch.setattr(areas, "write_confirmation", failing_write)
            with pytest.raises(RuntimeError, match="approval failure"):
                await changes.approve(
                    job_id=job,
                    actor="integration:2",
                    expected_revision=accepted.revision,
                )
            assert await reviews.load(job) == accepted
            assert not await areas.load_status(review=accepted)
        else:
            approved = await changes.approve(
                job_id=job, actor="integration:2", expected_revision=accepted.revision
            )
            (confirmed,) = await areas.load_confirmed(job_id=job)
            assert (
                confirmed.regions == (BOX,)
                and confirmed.confirmed_by == "integration:2"
            )
            assert approved.approved_revision == 2
    finally:
        await cleanup(engine, job)


async def test_simultaneous_accept_and_resize_do_not_certify_stale_geometry(
    engine, monkeypatch
):
    """Две вкладки читают одну ревизию; commit выигрывает только одна команда."""
    job = uuid4()
    reviews, areas = adapters(engine)
    changes = review_changes(engine, reviews, areas)
    original_load = reviews.load
    ready = asyncio.Event()
    readers = 0

    async def simultaneous_load(job_id):
        """Задерживает обе команды до чтения одинакового исходного снимка."""
        nonlocal readers
        current = await original_load(job_id)
        readers += 1
        if readers == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=10)
        return current

    monkeypatch.setattr(reviews, "load", simultaneous_load)
    try:
        await reviews.insert(initial(job))
        results = await asyncio.gather(
            changes.decide(
                job_id=job,
                finding_id="vlm:1",
                decision=Decision.ACCEPTED,
                actor="integration:1",
                expected_revision=0,
            ),
            changes.change_geometry(
                job_id=job,
                finding_id="vlm:1",
                regions=(FIXED,),
                callout_box=None,
                actor="integration:2",
                expected_revision=0,
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ReviewConflictError) for result in results) == 1
        assert sum(isinstance(result, ReviewSession) for result in results) == 1
        current = await original_load(job)
        statuses = await areas.load_status(review=current)
        if current.findings[0].decision is Decision.ACCEPTED:
            assert (
                len(statuses) == 1
                and statuses[0].valid
                and statuses[0].regions == (BOX,)
            )
        else:
            assert current.findings[0].display_regions == (FIXED,) and not statuses
    finally:
        await cleanup(engine, job)


async def seal(
    reviews: SqlAlchemyReviewRepository,
    original: ReviewSession,
) -> ReviewSession:
    """Принимает исходное замечание и утверждает актуальную редакцию."""
    accepted = original.decide(
        finding_id="vlm:1",
        decision=Decision.ACCEPTED,
        actor="integration:1",
        at=datetime.now(UTC),
        expected_revision=original.revision,
    )

    await reviews.update(
        accepted,
        expected_revision=original.revision,
    )

    approved = accepted.approve(
        actor="integration:1",
        at=datetime.now(UTC),
        expected_revision=accepted.revision,
    )

    await reviews.update(
        approved,
        expected_revision=accepted.revision,
    )

    return approved


@pytest.mark.asyncio
async def test_proposed_confirmation_survives_decision_and_approval(
    engine: AsyncEngine,
) -> None:
    """Явное подтверждение остаётся актуальным после решения без правки текста."""
    job = uuid4()

    reviews, areas = adapters(engine)
    opened = initial(job)

    try:
        await reviews.insert(opened)

        result = await ConfirmArea(
            reviews,
            areas,
        ).execute(
            job_id=job,
            finding_id="vlm:1",
            regions=(BOX,),
            mode=ConfirmationMode.PROPOSED,
            note="",
            actor="integration:2",
            expected_review_revision=0,
            expected_confirmation_revision=0,
        )

        assert result.confirmation_revision == 1

        await seal(
            reviews,
            opened,
        )

        (confirmed,) = await areas.load_confirmed(
            job_id=job,
        )

        assert confirmed.regions == (BOX,)
        assert confirmed.confirmed_by == "integration:2"

    finally:
        await cleanup(
            engine,
            job,
        )


@pytest.mark.asyncio
async def test_edit_and_text_reversion_both_invalidate_old_confirmation(
    engine: AsyncEngine,
) -> None:
    """Нельзя снова обучать на старой области после содержательной правки."""
    job = uuid4()

    reviews, areas = adapters(engine)
    opened = initial(job)

    try:
        await reviews.insert(opened)

        await ConfirmArea(
            reviews,
            areas,
        ).execute(
            job_id=job,
            finding_id="vlm:1",
            regions=(BOX,),
            mode=ConfirmationMode.PROPOSED,
            note="",
            actor="integration:2",
            expected_review_revision=0,
            expected_confirmation_revision=0,
        )

        approved = await seal(
            reviews,
            opened,
        )

        assert (
            len(
                await areas.load_confirmed(
                    job_id=job,
                )
            )
            == 1
        )

        edited = approved.edit(
            finding_id="vlm:1",
            text="Уточнение",
            normative_basis="СП 1",
            actor="integration:2",
            at=datetime.now(UTC),
            expected_revision=approved.revision,
        )

        await reviews.update(
            edited,
            expected_revision=approved.revision,
        )

        restored = edited.edit(
            finding_id="vlm:1",
            text="Замечание VLM",
            normative_basis="СП 1",
            actor="integration:2",
            at=datetime.now(UTC),
            expected_revision=edited.revision,
        )

        await reviews.update(
            restored,
            expected_revision=edited.revision,
        )

        approved_again = await seal(
            reviews,
            restored,
        )

        assert approved_again.approved_revision is not None

        assert (
            await areas.load_confirmed(
                job_id=job,
            )
            == ()
        )

    finally:
        await cleanup(
            engine,
            job,
        )


@pytest.mark.asyncio
async def test_correction_revocation_and_immutable_audit(
    engine: AsyncEngine,
) -> None:
    """Каждая корректировка сохраняет прежнюю рамку и автора действия."""
    job = uuid4()

    reviews, areas = adapters(engine)
    opened = initial(job)

    try:
        await reviews.insert(opened)

        confirm = ConfirmArea(
            reviews,
            areas,
        )

        await confirm.execute(
            job_id=job,
            finding_id="vlm:1",
            regions=(BOX,),
            mode=ConfirmationMode.PROPOSED,
            note="",
            actor="integration:1",
            expected_review_revision=0,
            expected_confirmation_revision=0,
        )

        corrected = await confirm.execute(
            job_id=job,
            finding_id="vlm:1",
            regions=(FIXED,),
            mode=ConfirmationMode.REDRAWN,
            note="Неверное место на листе",
            actor="integration:2",
            expected_review_revision=0,
            expected_confirmation_revision=1,
        )

        assert corrected.confirmation_revision == 2

        withdrawn = await RevokeArea(
            reviews,
            areas,
        ).execute(
            job_id=job,
            finding_id="vlm:1",
            actor="integration:3",
            reason="Область требует дополнительной проверки",
            expected_review_revision=0,
            expected_confirmation_revision=2,
        )

        assert not withdrawn.active
        assert withdrawn.confirmation_revision == 3

        with pytest.raises(ReviewConflictError):
            await RevokeArea(
                reviews,
                areas,
            ).execute(
                job_id=job,
                finding_id="vlm:1",
                actor="integration:3",
                reason="Повторный отзыв из старой вкладки",
                expected_review_revision=0,
                expected_confirmation_revision=2,
            )

        await seal(
            reviews,
            opened,
        )

        assert (
            await areas.load_confirmed(
                job_id=job,
            )
            == ()
        )

        # ВАЖНО:
        # AsyncConnection работает со строками SQL, а не
        # восстанавливает экземпляры ORM-моделей.
        #
        # Поэтому явно выбираем action и details,
        # затем получаем строки через execute().
        async with engine.connect() as connection:
            events = (
                await connection.execute(
                    select(
                        AreaConfirmationEventModel.action,
                        AreaConfirmationEventModel.details,
                    )
                    .where(AreaConfirmationEventModel.job_id == job)
                    .order_by(AreaConfirmationEventModel.revision)
                )
            ).all()

        assert [event.action for event in events] == [
            "confirmed",
            "corrected",
            "revoked",
        ]

        # Предыдущие координаты должны оставаться
        # в неизменяемой истории подтверждений.
        assert events[1].details["before"]["regions"][0]["x_min"] == 100.0

        # Отзыв сохраняет последнюю исправленную область,
        # но исключает её из активных подтверждений.
        assert events[2].details["after"]["regions"][0]["x_min"] == 400.0

    finally:
        await cleanup(
            engine,
            job,
        )


@pytest.mark.asyncio
async def test_two_simultaneous_confirmations_do_not_overwrite_each_other(
    engine: AsyncEngine,
) -> None:
    """Вторая вкладка получает конфликт независимо от выбора рамки."""
    job = uuid4()

    reviews, areas = adapters(engine)

    try:
        await reviews.insert(initial(job))

        common = {
            "job_id": job,
            "finding_id": "vlm:1",
            "actor": "integration:1",
            "expected_review_revision": 0,
            "expected_confirmation_revision": 0,
        }

        results = await asyncio.gather(
            ConfirmArea(
                reviews,
                areas,
            ).execute(
                **common,
                regions=(BOX,),
                mode=ConfirmationMode.PROPOSED,
                note="",
            ),
            ConfirmArea(
                reviews,
                areas,
            ).execute(
                **common,
                regions=(FIXED,),
                mode=ConfirmationMode.REDRAWN,
                note="Исправление координат",
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(value, Exception) for value in results) == 1

        assert sum(isinstance(value, ReviewConflictError) for value in results) == 1

        # Здесь нужны только номера записанных ревизий.
        # Не запрашиваем ORM-модель через AsyncConnection.
        async with engine.connect() as connection:
            revisions = (
                await connection.scalars(
                    select(AreaConfirmationEventModel.revision).where(
                        AreaConfirmationEventModel.job_id == job
                    )
                )
            ).all()

        assert revisions == [1]

    finally:
        await cleanup(
            engine,
            job,
        )


@pytest.mark.asyncio
async def test_review_revision_change_between_read_and_save_blocks_confirmation(
    engine: AsyncEngine,
) -> None:
    """Если решение обновилось после чтения, устаревшая рамка не записывается."""
    from pdrd_experience_service.domain.area_confirmation import (
        build_confirmation,
    )

    job = uuid4()

    reviews, areas = adapters(engine)
    opened = initial(job)

    try:
        await reviews.insert(opened)

        prepared = build_confirmation(
            session=opened,
            finding_id="vlm:1",
            regions=(BOX,),
            mode=ConfirmationMode.PROPOSED,
            note="",
            actor="integration:2",
            at=datetime.now(UTC),
            expected_review_revision=0,
        )

        accepted = opened.decide(
            finding_id="vlm:1",
            decision=Decision.ACCEPTED,
            actor="integration:1",
            at=datetime.now(UTC),
            expected_revision=0,
        )

        await reviews.update(
            accepted,
            expected_revision=0,
        )

        with pytest.raises(ReviewConflictError):
            await areas.save(
                confirmation=prepared,
                expected_confirmation_revision=0,
            )

        # Проверяем отсутствие событий аудита от
        # неуспешной конкурентной операции.
        async with engine.connect() as connection:
            revisions = (
                await connection.scalars(
                    select(AreaConfirmationEventModel.revision).where(
                        AreaConfirmationEventModel.job_id == job
                    )
                )
            ).all()

        assert revisions == []

    finally:
        await cleanup(
            engine,
            job,
        )
