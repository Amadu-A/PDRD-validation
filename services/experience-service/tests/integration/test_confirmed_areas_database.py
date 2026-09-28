# services/experience-service/tests/integration/test_confirmed_areas_database.py

"""Проверяет реальное хранение, отзыв и конкуренцию подтверждений PostgreSQL.

Запускается только на изолированном Compose со временной тестовой базой.
Никакие тесты не выполняют SQL в рабочем PostgreSQL PDRD.
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
    ReviewSessionModel,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
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

            assert revision == "20260928_0002"

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

    reviews = SqlAlchemyReviewRepository(sessions)

    areas = SqlAlchemyConfirmedAreasRepository(
        sessions,
        reviews,
    )

    return reviews, areas


async def cleanup(
    engine: AsyncEngine,
    job_id: UUID,
) -> None:
    """Удаляет только тестовый job; связанные тестовые события удаляет FK."""
    async with engine.begin() as connection:
        await connection.execute(
            delete(ReviewSessionModel).where(ReviewSessionModel.job_id == job_id)
        )


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

        await seal(reviews, opened)

        (confirmed,) = await areas.load_confirmed(
            job_id=job,
        )

        assert confirmed.regions == (BOX,)
        assert confirmed.confirmed_by == "integration:2"

    finally:
        await cleanup(engine, job)


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

        approved = await seal(reviews, opened)

        assert len(await areas.load_confirmed(job_id=job)) == 1

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
        await cleanup(engine, job)


@pytest.mark.asyncio
async def test_correction_revocation_and_immutable_audit(
    engine: AsyncEngine,
) -> None:
    """Каждая корректировка сохраняет старую рамку и идентификатор автора."""
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

        await seal(reviews, opened)

        assert (
            await areas.load_confirmed(
                job_id=job,
            )
            == ()
        )

        async with engine.connect() as connection:
            events = (
                await connection.scalars(
                    select(AreaConfirmationEventModel)
                    .where(AreaConfirmationEventModel.job_id == job)
                    .order_by(AreaConfirmationEventModel.revision)
                )
            ).all()

        assert [event.action for event in events] == [
            "confirmed",
            "corrected",
            "revoked",
        ]

        assert events[1].details["before"]["regions"][0]["x_min"] == 100.0

        assert events[2].details["after"]["regions"][0]["x_min"] == 400.0

    finally:
        await cleanup(engine, job)


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

        async with engine.connect() as connection:
            entries = (
                await connection.scalars(
                    select(AreaConfirmationEventModel).where(
                        AreaConfirmationEventModel.job_id == job
                    )
                )
            ).all()

        assert len(entries) == 1

    finally:
        await cleanup(engine, job)


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

        async with engine.connect() as connection:
            events = (
                await connection.scalars(
                    select(AreaConfirmationEventModel).where(
                        AreaConfirmationEventModel.job_id == job
                    )
                )
            ).all()

        assert events == []

    finally:
        await cleanup(engine, job)
