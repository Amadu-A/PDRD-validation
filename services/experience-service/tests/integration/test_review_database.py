# services/experience-service/tests/integration/test_review_database.py

"""Интеграционные проверки PostgreSQL-хранилища Human Review.

Для чего нужен файл:
- проверяет, что первая миграция действительно применена к PostgreSQL;
- испытывает сохранение и восстановление замечаний и истории действий;
- проверяет защиту от одновременного изменения одного отчёта;
- запрещает случайное выполнение тестов на рабочей базе PDRD.

Интеграционные сценарии запускаются только после установки
PDRD_RUN_DATABASE_TESTS=1 и проверки адреса изолированной базы.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.models import (
    ReviewEventModel,
    ReviewSessionModel,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)
from sqlalchemy import delete, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = pytest.mark.database

TEST_DATABASE_NAME = "pdrd_experience_test"
TEST_DATABASE_USER = "experience_test"
TEST_DATABASE_HOST = "experience-test-postgres"

EXPECTED_MIGRATION = "20260925_0001"


def validate_isolated_database_url(raw_url: str) -> URL:
    """Разрешает подключение исключительно к выделенной тестовой базе.

    Проверяем одновременно имя БД, пользователя, сервер и драйвер.
    Одного имени тестовой базы недостаточно: переменная окружения
    потенциально может быть настроена ошибочно.
    """
    try:
        url = make_url(raw_url)
    except ArgumentError as error:
        raise ValueError("Передан некорректный URL тестовой базы.") from error

    if (
        url.drivername != "postgresql+asyncpg"
        or url.database != TEST_DATABASE_NAME
        or url.username != TEST_DATABASE_USER
        or url.host != TEST_DATABASE_HOST
        or url.port not in (None, 5432)
    ):
        raise ValueError(
            "Интеграционные тесты разрешены только "
            "для изолированного PostgreSQL Experience."
        )

    return url


@pytest.mark.parametrize(
    "raw_url",
    [
        ("postgresql+asyncpg://pdrd:secret@postgres:5432/pdrd"),
        (
            "postgresql+asyncpg://experience_test:secret"
            "@localhost:5432/pdrd_experience_test"
        ),
        (
            "postgresql+asyncpg://experience_test:secret"
            "@experience-test-postgres:5432/pdrd"
        ),
    ],
)
def test_reject_nonisolated_database_urls(
    raw_url: str,
) -> None:
    """Рабочий PostgreSQL и другие базы не должны проходить проверку."""
    with pytest.raises(
        ValueError,
        match="изолированного",
    ):
        validate_isolated_database_url(raw_url)


def test_accept_exact_isolated_database_identity() -> None:
    """Правильные реквизиты временной базы проходят предварительную проверку."""
    url = validate_isolated_database_url(
        "postgresql+asyncpg://experience_test:test"
        "@experience-test-postgres:5432/pdrd_experience_test"
    )

    assert url.database == TEST_DATABASE_NAME
    assert url.username == TEST_DATABASE_USER
    assert url.host == TEST_DATABASE_HOST


@pytest_asyncio.fixture
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Создаёт подключение после обязательной проверки изоляции.

    Фикстура не создаёт и не удаляет базу данных. За жизненный цикл
    временного PostgreSQL отвечает отдельный Docker Compose.
    """
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Реальный PostgreSQL требует PDRD_RUN_DATABASE_TESTS=1.")

    raw_url = os.environ.get(
        "EXPERIENCE_SERVICE_TEST_DATABASE_URL",
    )

    if not raw_url:
        pytest.skip("Не задан EXPERIENCE_SERVICE_TEST_DATABASE_URL.")

    url = validate_isolated_database_url(raw_url)

    migration_url = os.environ.get(
        "EXPERIENCE_SERVICE_DATABASE_URL",
    )

    if migration_url != raw_url:
        raise ValueError(
            "Миграция и тесты должны использовать одну изолированную базу данных."
        )

    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
    )

    try:
        async with engine.connect() as connection:
            identity = (
                await connection.execute(
                    text("SELECT current_database(), current_user")
                )
            ).one()

            assert identity[0] == TEST_DATABASE_NAME
            assert identity[1] == TEST_DATABASE_USER

            # Проверяем реальное применение первой миграции.
            # Отсутствующая таблица должна приводить к ошибке теста.
            current_revision = await connection.scalar(
                text("SELECT version_num FROM experience.alembic_version_experience")
            )

            assert current_revision == EXPECTED_MIGRATION

        yield engine

    finally:
        await engine.dispose()


def new_review(
    job_id: UUID,
) -> ReviewSession:
    """Создаёт тестовый отчёт с одним исходным замечанием VLM."""
    return ReviewSession.open(
        job_id=job_id,
        document_id=uuid4(),
        source_filename="test-drawing.pdf",
        source_sha256="b" * 64,
        originals=(
            OriginalFinding(
                finding_id="vlm:test:1",
                page_number=1,
                text="Проверяемое замечание VLM",
                normative_basis="СП TEST",
            ),
        ),
        rendered_pages=(1,),
        actor="integration:user:1",
        at=datetime.now(UTC),
    )


def repository_for(
    engine: AsyncEngine,
) -> SqlAlchemyReviewRepository:
    """Создаёт репозиторий с отдельной сессией на каждую операцию."""
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    return SqlAlchemyReviewRepository(
        factory,
    )


async def remove_test_review(
    engine: AsyncEngine,
    job_id: UUID,
) -> None:
    """Удаляет только созданный текущим тестом отчёт.

    Не выполняет TRUNCATE, DROP или очистку общих таблиц.
    Каскадное удаление ограничено событиями конкретного job_id.
    """
    async with engine.begin() as connection:
        await connection.execute(
            delete(ReviewSessionModel).where(
                ReviewSessionModel.job_id == job_id,
            )
        )


@pytest.mark.asyncio
async def test_insert_restore_approve_and_audit_on_postgres(
    test_engine: AsyncEngine,
) -> None:
    """Проверяет полную цепочку сохранения и утверждения отчёта.

    Операционный Review хранит даже замечания без координат.
    Отбор таких находок в обучающую Experience выполняется отдельно.
    """
    job_id = uuid4()

    repository = repository_for(
        test_engine,
    )

    original = new_review(
        job_id,
    )

    try:
        await repository.insert(original)

        restored = await repository.load(job_id)

        assert restored == original
        assert restored is not original

        # Повторное открытие того же отчёта через INSERT запрещено.
        with pytest.raises(ReviewConflictError):
            await repository.insert(original)

        accepted = original.decide(
            finding_id="vlm:test:1",
            decision=Decision.ACCEPTED,
            actor="integration:user:2",
            at=datetime.now(UTC),
            expected_revision=0,
        )

        await repository.update(
            accepted,
            expected_revision=0,
        )

        approved = accepted.approve(
            actor="integration:user:2",
            at=datetime.now(UTC),
            expected_revision=1,
        )

        await repository.update(
            approved,
            expected_revision=1,
        )

        actual = await repository.load(job_id)

        assert actual == approved
        assert actual is not None
        assert actual.approved_revision == 2
        assert len(actual.history) == 3

        # Убеждаемся, что утверждённая редакция пригодна
        # для передачи будущему генератору итогового PDF.
        snapshot = actual.accepted_for_pdf()

        assert snapshot.revision == 2
        assert len(snapshot.findings) == 1
        assert snapshot.findings[0].finding_id == "vlm:test:1"

        async with test_engine.connect() as connection:
            result = await connection.execute(
                select(
                    ReviewEventModel.session_revision,
                    ReviewEventModel.action,
                )
                .where(
                    ReviewEventModel.job_id == job_id,
                )
                .order_by(
                    ReviewEventModel.session_revision,
                )
            )

            events = result.all()

        assert events == [
            (0, "opened"),
            (1, "decided"),
            (2, "approved"),
        ]

    finally:
        await remove_test_review(
            test_engine,
            job_id,
        )


@pytest.mark.asyncio
async def test_two_concurrent_updates_have_one_winner(
    test_engine: AsyncEngine,
) -> None:
    """Два одновременных решения не могут незаметно заменить друг друга.

    Обе транзакции начинают работу с revision=0.
    PostgreSQL должен принять ровно одно изменение.
    Вторая операция получает ReviewConflictError.
    """
    job_id = uuid4()

    repository = repository_for(
        test_engine,
    )

    original = new_review(
        job_id,
    )

    try:
        await repository.insert(
            original,
        )

        accepted = original.decide(
            finding_id="vlm:test:1",
            decision=Decision.ACCEPTED,
            actor="integration:user:A",
            at=datetime.now(UTC),
            expected_revision=0,
        )

        rejected = original.decide(
            finding_id="vlm:test:1",
            decision=Decision.REJECTED,
            actor="integration:user:B",
            at=datetime.now(UTC),
            expected_revision=0,
        )

        # Репозиторий создаёт независимые AsyncSession,
        # поэтому здесь действительно конкурируют транзакции.
        results = await asyncio.gather(
            repository.update(
                accepted,
                expected_revision=0,
            ),
            repository.update(
                rejected,
                expected_revision=0,
            ),
            return_exceptions=True,
        )

        successful = sum(result is None for result in results)

        conflicts = sum(isinstance(result, ReviewConflictError) for result in results)

        assert successful == 1
        assert conflicts == 1

        actual = await repository.load(
            job_id,
        )

        assert actual is not None
        assert actual.revision == 1
        assert len(actual.history) == 2

        assert actual.findings[0].decision in (
            Decision.ACCEPTED,
            Decision.REJECTED,
        )

        # Не допускаем скрытого появления второго события
        # при откате проигравшей транзакции.
        async with test_engine.connect() as connection:
            revisions = (
                await connection.scalars(
                    select(
                        ReviewEventModel.session_revision,
                    )
                    .where(
                        ReviewEventModel.job_id == job_id,
                    )
                    .order_by(
                        ReviewEventModel.session_revision,
                    )
                )
            ).all()

        assert revisions == [0, 1]

    finally:
        await remove_test_review(
            test_engine,
            job_id,
        )
