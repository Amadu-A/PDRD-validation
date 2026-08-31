# services/api-gateway/tests/unit/test_dispatch_outbox.py

"""Unit-тесты transactional outbox dispatcher."""

from types import TracebackType
from uuid import UUID, uuid4

from pdrd_api_gateway.application.ports.messaging import (
    OutboxPublishError,
)
from pdrd_api_gateway.application.use_cases.dispatch_outbox import (
    DispatchOutbox,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)
from pdrd_api_gateway.domain.outbox import OutboxMessage


class FakeJobRepository:
    """In-memory job repository."""

    def __init__(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет исходный job."""
        self.jobs = {
            job.id: job,
        }

    async def add(
        self,
        job: AnalysisJob,
    ) -> None:
        """Добавляет job."""
        self.jobs[job.id] = job

    async def get(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job."""
        return self.jobs.get(
            job_id,
        )

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Обновляет job."""
        self.jobs[job.id] = job


class FakeOutboxRepository:
    """In-memory outbox repository."""

    def __init__(
        self,
        message: OutboxMessage,
    ) -> None:
        """Сохраняет исходное сообщение."""
        self.messages = {
            message.id: message,
        }

    async def add(
        self,
        message: OutboxMessage,
    ) -> None:
        """Добавляет message."""
        self.messages[message.id] = message

    async def get_pending(
        self,
        *,
        limit: int,
    ) -> list[OutboxMessage]:
        """Возвращает pending messages."""
        return [
            message
            for message in self.messages.values()
            if message.published_at is None
        ][:limit]

    async def update(
        self,
        message: OutboxMessage,
    ) -> None:
        """Обновляет message."""
        self.messages[message.id] = message


class FakeUnitOfWork:
    """In-memory transaction."""

    def __init__(
        self,
        *,
        job: AnalysisJob,
        message: OutboxMessage,
    ) -> None:
        """Создаёт repositories."""
        self.analysis_jobs = FakeJobRepository(
            job,
        )

        self.outbox = FakeOutboxRepository(
            message,
        )

        self.committed = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        """Открывает transaction."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает transaction."""
        del exc_type
        del exc_value
        del traceback

    async def commit(self) -> None:
        """Фиксирует transaction."""
        self.committed = True

    async def rollback(self) -> None:
        """Имитирует rollback."""


class RecordingPublisher:
    """Успешный fake publisher."""

    def __init__(self) -> None:
        """Создаёт список опубликованных IDs."""
        self.message_ids: list[UUID] = []

    async def publish(
        self,
        message: OutboxMessage,
    ) -> None:
        """Запоминает опубликованное сообщение."""
        self.message_ids.append(
            message.id,
        )


class FailingPublisher:
    """Publisher, имитирующий RabbitMQ failure."""

    async def publish(
        self,
        message: OutboxMessage,
    ) -> None:
        """Всегда возвращает контролируемую ошибку."""
        del message

        raise OutboxPublishError(
            "broker unavailable",
        )


async def test_successful_dispatch_marks_job_queued() -> None:
    """Проверяет успешную публикацию и transition job."""
    job = AnalysisJob.create(
        document_id=uuid4(),
    )

    message = OutboxMessage.analysis_requested(
        job_id=job.id,
    )

    unit_of_work = FakeUnitOfWork(
        job=job,
        message=message,
    )

    publisher = RecordingPublisher()

    use_case = DispatchOutbox(
        unit_of_work_factory=lambda: unit_of_work,
        publisher=publisher,
    )

    report = await use_case.execute(
        limit=10,
    )

    assert report.selected == 1
    assert report.published == 1
    assert report.failed == 0

    assert job.status is AnalysisJobStatus.QUEUED
    assert message.published_at is not None
    assert message.attempt_count == 1
    assert publisher.message_ids == [
        message.id,
    ]
    assert unit_of_work.committed is True


async def test_failed_dispatch_keeps_job_pending() -> None:
    """Проверяет сохранение job при ошибке message broker."""
    job = AnalysisJob.create(
        document_id=uuid4(),
    )

    message = OutboxMessage.analysis_requested(
        job_id=job.id,
    )

    unit_of_work = FakeUnitOfWork(
        job=job,
        message=message,
    )

    use_case = DispatchOutbox(
        unit_of_work_factory=lambda: unit_of_work,
        publisher=FailingPublisher(),
    )

    report = await use_case.execute(
        limit=10,
    )

    assert report.selected == 1
    assert report.published == 0
    assert report.failed == 1

    assert job.status is AnalysisJobStatus.PENDING
    assert message.published_at is None
    assert message.attempt_count == 1
    assert message.last_error == "broker unavailable"
    assert unit_of_work.committed is True
