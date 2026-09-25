# services/experience-service/tests/unit/test_review_persistence.py

"""Модульные проверки постоянного хранения Human Review.

Назначение файла:
- проверять преобразование доменных объектов в JSONB и обратно;
- контролировать структуру PostgreSQL-таблиц;
- проверять наличие атомарных операций и контроль ревизий;
- закреплять правильный порядок INSERT родителя и дочернего события;
- не допускать маскировки ошибок внешнего ключа ошибками конкуренции.

Настоящее поведение PostgreSQL дополнительно проверяется
отдельными интеграционными тестами.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    Rectangle,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.codec import (
    event_from_json,
    event_to_json,
    snapshot_from_json,
    snapshot_to_json,
)
from pdrd_experience_service.infrastructure.database.models import (
    Base,
    ReviewEventModel,
    ReviewSessionModel,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

NOW = datetime(2026, 9, 25, tzinfo=UTC)

JOB = UUID(int=101)
DOC = UUID(int=102)


def session_with_full_history() -> ReviewSession:
    """Формирует отчёт с исправленной VLM-находкой и принятым Gold.

    Используется для проверки сохранения всех промежуточных
    ревизий и неизменности первоначальных формулировок.
    """
    session = ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="drawing.pdf",
        source_sha256="a" * 64,
        originals=(
            OriginalFinding(
                "v1",
                2,
                "Исходная VLM",
                "СП 1",
            ),
        ),
        rendered_pages=(2,),
        actor="engineer:1",
        at=NOW,
    )

    session = session.edit(
        finding_id="v1",
        text="Исправлено",
        normative_basis="СП 2",
        actor="engineer:2",
        at=NOW,
        expected_revision=session.revision,
    )

    session = session.add_manual(
        finding_id=f"manual:{UUID(int=77)}",
        page_number=2,
        text="Пропуск VLM",
        normative_basis="СП 3",
        issue_box=Rectangle(
            100,
            110,
            200,
            210,
        ),
        callout_box=Rectangle(
            450,
            460,
            750,
            700,
        ),
        actor="engineer:3",
        at=NOW,
        expected_revision=session.revision,
    )

    for finding in session.findings:
        session = session.decide(
            finding_id=finding.finding_id,
            decision=Decision.ACCEPTED,
            actor="engineer:1",
            at=NOW,
            expected_revision=session.revision,
        )

    return session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )


def initial_session() -> ReviewSession:
    """Создаёт исходный отчёт без замечаний для проверки INSERT."""
    return ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="source.pdf",
        source_sha256="a" * 64,
        originals=(),
        rendered_pages=(1,),
        actor="engineer:1",
        at=NOW,
    )


def test_snapshot_roundtrip_preserves_original_edit_gold_geometry_and_approval() -> (
    None
):
    """Проверяет восстановление первоначального текста, Gold и утверждения."""
    source = session_with_full_history()

    payload = snapshot_to_json(
        source,
    )

    assert "history" not in payload
    assert payload["findings"][0]["origin"] == "vlm"

    restored_events = tuple(
        event_from_json(
            action=event.action.value,
            actor=event.actor,
            occurred_at=event.occurred_at,
            revision=event.session_revision,
            details=event_to_json(event),
        )
        for event in source.history
    )

    restored = snapshot_from_json(
        payload,
        restored_events,
    )

    assert restored == source
    assert restored.accepted_for_pdf() == source.accepted_for_pdf()
    assert restored.findings[0].experience_tag == "edited"
    assert restored.findings[1].experience_tag == "gold"

    assert restored.findings[1].issue_box == Rectangle(
        100,
        110,
        200,
        210,
    )


def test_roundtrip_rejects_naive_audit_metadata() -> None:
    """Восстановление запрещает временные метки без часового пояса."""
    source = session_with_full_history()

    data = snapshot_to_json(
        source,
    )

    data["opened_at"] = "2026-09-25T00:00:00"

    with pytest.raises(
        ValueError,
        match="timezone",
    ):
        snapshot_from_json(
            data,
            source.history,
        )


def test_orm_uses_separate_experience_schema_and_append_only_event_key() -> None:
    """Таблицы Experience не должны изменять схемы других сервисов."""
    assert set(Base.metadata.tables) == {
        "experience.review_sessions",
        "experience.review_events",
    }

    assert [column.name for column in ReviewEventModel.__table__.primary_key] == [
        "job_id",
        "session_revision",
    ]

    sessions_sql = str(
        CreateTable(
            ReviewSessionModel.__table__,
        ).compile(
            dialect=postgresql.dialect(),
        )
    )

    events_sql = str(
        CreateTable(
            ReviewEventModel.__table__,
        ).compile(
            dialect=postgresql.dialect(),
        )
    )

    assert "experience.review_sessions" in sessions_sql
    assert "experience.review_events" in events_sql

    assert "JSONB" in sessions_sql and "JSONB" in events_sql

    assert "ck_review_sessions_approval" in sessions_sql


def test_repository_requires_one_audited_revision_per_update() -> None:
    """Одна команда изменения должна создавать одну ревизию."""
    repository = SqlAlchemyReviewRepository(
        session_factory=lambda: None,  # type: ignore[arg-type]
    )

    source = session_with_full_history()

    with pytest.raises(
        ValueError,
        match="одну новую ревизию",
    ):
        asyncio.run(
            repository.update(
                source,
                expected_revision=0,
            )
        )


class FakeDriverError(Exception):
    """Имитирует ошибку PostgreSQL с конкретным кодом SQLSTATE."""

    def __init__(
        self,
        sqlstate: str,
    ) -> None:
        """Сохраняет код, используемый SQLAlchemy для классификации ошибок."""
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class FakeDatabase:
    """Имитация сессии с наблюдаемым порядком операций.

    Этот объект не моделирует конкурентность PostgreSQL.
    Он нужен исключительно для проверки того, что код
    явно выполняет flush() перед добавлением дочерней записи.
    """

    def __init__(
        self,
        changed: UUID | None = JOB,
        flush_error: IntegrityError | None = None,
    ) -> None:
        """Позволяет имитировать успешный INSERT и ошибки SQLSTATE."""
        self.changed = changed
        self.flush_error = flush_error

        self.added: list[object] = []
        self.operations: list[str] = []

        self.statement: object | None = None

    async def __aenter__(
        self,
    ) -> "FakeDatabase":
        """Имитирует открытие сессии или транзакции."""
        return self

    async def __aexit__(
        self,
        *args: object,
    ) -> None:
        """Имитирует завершение контекста без подключения к PostgreSQL."""

    def begin(
        self,
    ) -> "FakeDatabase":
        """Имитирует границу транзакции SQLAlchemy."""
        return self

    def add(
        self,
        object_: object,
    ) -> None:
        """Запоминает добавленный ORM-объект и порядок операций."""
        self.added.append(
            object_,
        )

        self.operations.append(
            type(object_).__name__,
        )

    async def flush(
        self,
    ) -> None:
        """Фиксирует отправку накопленных ORM-операций в имитации."""
        self.operations.append(
            "flush",
        )

        if self.flush_error is not None:
            raise self.flush_error

    async def scalar(
        self,
        statement: object,
    ) -> UUID | None:
        """Сохраняет SQL-запрос с проверкой ревизии."""
        self.statement = statement

        return self.changed


@pytest.mark.asyncio
async def test_insert_flushes_parent_before_adding_audit_event() -> None:
    """Событие аудита добавляется только после отправки родителя.

    Это регрессионный тест ошибки ForeignKeyViolationError,
    обнаруженной при первом запуске на настоящем PostgreSQL.
    """
    database = FakeDatabase()

    await SqlAlchemyReviewRepository(
        lambda: database,  # type: ignore[arg-type]
    ).insert(
        initial_session(),
    )

    assert len(database.added) == 2

    assert isinstance(
        database.added[0],
        ReviewSessionModel,
    )

    assert isinstance(
        database.added[1],
        ReviewEventModel,
    )

    assert database.operations == [
        "ReviewSessionModel",
        "flush",
        "ReviewEventModel",
    ]

    assert database.added[1].session_revision == 0


@pytest.mark.asyncio
async def test_duplicate_insert_maps_unique_violation_to_review_conflict() -> None:
    """Повторный job_id преобразуется в конфликт существующего отчёта."""
    database = FakeDatabase(
        flush_error=IntegrityError(
            "INSERT",
            {},
            FakeDriverError("23505"),
        ),
    )

    with pytest.raises(
        ReviewConflictError,
        match="уже существует",
    ):
        await SqlAlchemyReviewRepository(
            lambda: database,  # type: ignore[arg-type]
        ).insert(
            initial_session(),
        )

    assert database.operations == [
        "ReviewSessionModel",
        "flush",
    ]


@pytest.mark.asyncio
async def test_foreign_key_error_is_not_reported_as_duplicate_review() -> None:
    """Ошибка внешнего ключа должна сохранить первоначальную причину."""
    database = FakeDatabase(
        flush_error=IntegrityError(
            "INSERT",
            {},
            FakeDriverError("23503"),
        ),
    )

    with pytest.raises(
        IntegrityError,
    ):
        await SqlAlchemyReviewRepository(
            lambda: database,  # type: ignore[arg-type]
        ).insert(
            initial_session(),
        )

    assert database.operations == [
        "ReviewSessionModel",
        "flush",
    ]


@pytest.mark.asyncio
async def test_update_guards_expected_revision_and_adds_only_new_audit_event() -> None:
    """Конкурентное обновление проверяется непосредственно условием SQL."""
    approved = session_with_full_history()

    database = FakeDatabase()

    await SqlAlchemyReviewRepository(
        lambda: database,  # type: ignore[arg-type]
    ).update(
        approved,
        expected_revision=approved.revision - 1,
    )

    assert len(database.added) == 1

    event = database.added[0]

    assert isinstance(
        event,
        ReviewEventModel,
    )

    assert event.action == "approved"

    compiled = database.statement.compile(
        dialect=postgresql.dialect(),
    )

    assert "review_sessions.revision =" in str(compiled)

    assert approved.revision - 1 in compiled.params.values()


@pytest.mark.asyncio
async def test_stale_database_update_fails_before_appending_an_event() -> None:
    """Устаревшая ревизия не должна создавать новое событие аудита."""
    approved = session_with_full_history()

    database = FakeDatabase(
        changed=None,
    )

    with pytest.raises(
        ReviewConflictError,
        match="другим пользователем",
    ):
        await SqlAlchemyReviewRepository(
            lambda: database,  # type: ignore[arg-type]
        ).update(
            approved,
            expected_revision=approved.revision - 1,
        )

    assert database.added == []
