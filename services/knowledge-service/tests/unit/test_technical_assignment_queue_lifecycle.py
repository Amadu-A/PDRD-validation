# services/knowledge-service/tests/unit/test_technical_assignment_queue_lifecycle.py

"""Unit tests bounded T queue lifecycle и reconciliation."""

from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import Any
from uuid import UUID, uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.recover_stale_technical_assignments import (
    RecoverStaleTechnicalAssignments,
)
from pdrd_knowledge_service.core.settings import get_settings
from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
)
from pdrd_knowledge_service.infrastructure.messaging.celery_app import (
    celery_app,
    technical_queue,
)
from pdrd_knowledge_service.infrastructure.messaging.publisher import (
    CeleryTechnicalAssignmentOutboxPublisher,
)

NOW = datetime(
    2026,
    9,
    11,
    6,
    0,
    tzinfo=UTC,
)


class FakeCelery:
    """Фиксирует T send_task без подключения к RabbitMQ."""

    def __init__(self) -> None:
        """Создаёт пустой вызов."""
        self.call: dict[str, Any] | None = None

    def send_task(
        self,
        task_name: str,
        **kwargs: Any,
    ) -> None:
        """Сохраняет параметры публикации."""
        self.call = {
            "task_name": task_name,
            **kwargs,
        }


class FakeAssignmentRepository:
    """In-memory T repository."""

    def __init__(
        self,
        state: dict[UUID, TechnicalAssignment],
    ) -> None:
        """Сохраняет state."""
        self.state = state

    async def get_recoverable(
        self,
        *,
        stale_indexing_before: object,
        deadline_before: object,
        limit: int,
    ) -> list[TechnicalAssignment]:
        """Возвращает fake batch."""
        del stale_indexing_before, deadline_before
        return list(self.state.values())[:limit]

    async def update(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Сохраняет assignment."""
        self.state[assignment.technical_assignment_id] = assignment


class FakeTechnicalOutbox:
    """Собирает recovery T-events."""

    def __init__(self) -> None:
        """Создаёт пустой список."""
        self.messages: list[TechnicalAssignmentOutboxMessage] = []

    async def add(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Сохраняет event."""
        self.messages.append(
            message,
        )


class FakeTechnicalUnitOfWork:
    """Fake T UoW."""

    def __init__(
        self,
        state: dict[UUID, TechnicalAssignment],
        outbox: FakeTechnicalOutbox,
    ) -> None:
        """Сохраняет repositories."""
        self.assignments = FakeAssignmentRepository(
            state,
        )
        self.outbox = outbox
        self.sections = object()

    async def __aenter__(self) -> "FakeTechnicalUnitOfWork":
        """Открывает UoW."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Закрывает UoW."""

    async def commit(self) -> None:
        """Имитирует commit."""

    async def rollback(self) -> None:
        """Имитирует rollback."""


class FakeTechnicalFactory:
    """Factory fake T UoW."""

    def __init__(
        self,
        state: dict[UUID, TechnicalAssignment],
        outbox: FakeTechnicalOutbox,
    ) -> None:
        """Сохраняет state."""
        self.state = state
        self.outbox = outbox

    def __call__(self) -> FakeTechnicalUnitOfWork:
        """Возвращает UoW."""
        return FakeTechnicalUnitOfWork(
            self.state,
            self.outbox,
        )


def build_assignment(
    *,
    status: TechnicalAssignmentIndexStatus,
    created_at: datetime,
    updated_at: datetime,
) -> TechnicalAssignment:
    """Создаёт synthetic ТЗ нужного lifecycle state."""
    return TechnicalAssignment(
        technical_assignment_id=uuid4(),
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        original_name="ТЗ.pdf",
        mime_type=PDF_MIME_TYPE,
        size_bytes=100,
        sha256="a" * 64,
        index_status=status,
        index_error=None,
        indexed_at=None,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_technical_queue_declares_broker_ttl() -> None:
    """T queue имеет RabbitMQ TTL и queue expiry."""
    settings = get_settings().technical_assignment_queue
    arguments = technical_queue.queue_arguments

    assert arguments is not None
    assert arguments["x-message-ttl"] == settings.message_ttl_seconds * 1000
    assert arguments["x-expires"] == settings.queue_expires_seconds * 1000

    annotation = celery_app.conf.task_annotations[
        "pdrd.knowledge.technical_assignment.index"
    ]
    assert annotation["soft_time_limit"] == settings.task_soft_time_limit_seconds
    assert annotation["time_limit"] == settings.task_hard_time_limit_seconds
    assert settings.max_runtime_seconds < 1800
    assert settings.task_hard_time_limit_seconds == 1800


@pytest.mark.asyncio
async def test_t_outbox_publish_sets_celery_expiration() -> None:
    """T task получает Celery expires поверх broker TTL."""
    settings = get_settings().technical_assignment_queue
    fake_celery = FakeCelery()

    publisher = CeleryTechnicalAssignmentOutboxPublisher(
        celery_app=fake_celery,  # type: ignore[arg-type]
        queue_settings=settings,
    )

    message = TechnicalAssignmentOutboxMessage.index_requested(
        message_id=uuid4(),
        technical_assignment_id=uuid4(),
        created_at=NOW,
    )

    await publisher.publish(
        message,
    )

    assert fake_celery.call is not None
    assert fake_celery.call["expires"] == settings.task_expires_seconds


@pytest.mark.asyncio
async def test_stale_indexing_returns_to_queue() -> None:
    """Потерянный T worker восстанавливается через transactional outbox."""
    assignment = build_assignment(
        status=TechnicalAssignmentIndexStatus.INDEXING,
        created_at=NOW
        - timedelta(
            minutes=5,
        ),
        updated_at=NOW
        - timedelta(
            minutes=3,
        ),
    )

    state = {
        assignment.technical_assignment_id: assignment,
    }
    outbox = FakeTechnicalOutbox()

    recovery = RecoverStaleTechnicalAssignments(
        unit_of_work_factory=FakeTechnicalFactory(
            state,
            outbox,
        ),  # type: ignore[arg-type]
        max_runtime_seconds=1740,
        stale_indexing_seconds=120,
        clock=lambda: NOW,
    )

    report = await recovery.execute(
        limit=50,
    )

    changed = state[assignment.technical_assignment_id]

    assert report.requeued == 1
    assert report.failed == 0
    assert changed.index_status is TechnicalAssignmentIndexStatus.QUEUED
    assert len(outbox.messages) == 1


@pytest.mark.asyncio
async def test_expired_queued_t_is_failed() -> None:
    """T task старше deadline не остаётся queued навечно."""
    assignment = build_assignment(
        status=TechnicalAssignmentIndexStatus.QUEUED,
        created_at=NOW
        - timedelta(
            minutes=31,
        ),
        updated_at=NOW
        - timedelta(
            minutes=31,
        ),
    )

    state = {
        assignment.technical_assignment_id: assignment,
    }
    outbox = FakeTechnicalOutbox()

    recovery = RecoverStaleTechnicalAssignments(
        unit_of_work_factory=FakeTechnicalFactory(
            state,
            outbox,
        ),  # type: ignore[arg-type]
        max_runtime_seconds=1740,
        stale_indexing_seconds=120,
        clock=lambda: NOW,
    )

    report = await recovery.execute(
        limit=50,
    )

    changed = state[assignment.technical_assignment_id]

    assert report.failed == 1
    assert changed.index_status is TechnicalAssignmentIndexStatus.FAILED
    assert changed.index_error is not None
    assert outbox.messages == []
