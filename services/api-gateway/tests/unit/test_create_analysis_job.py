# services/api-gateway/tests/unit/test_create_analysis_job.py

"""Unit-тест CreateAnalysisJob без PostgreSQL."""

from types import TracebackType
from uuid import UUID, uuid4

from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)
from pdrd_api_gateway.domain.outbox import OutboxMessage


class FakeAnalysisJobRepository:
    """In-memory repository заданий."""

    def __init__(self) -> None:
        """Создаёт пустое хранилище."""
        self.jobs: dict[UUID, AnalysisJob] = {}

    async def add(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет job."""
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
    """In-memory transactional outbox."""

    def __init__(self) -> None:
        """Создаёт пустой outbox."""
        self.messages: dict[UUID, OutboxMessage] = {}

    async def add(
        self,
        message: OutboxMessage,
    ) -> None:
        """Сохраняет message."""
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
    """In-memory Unit of Work."""

    def __init__(self) -> None:
        """Создаёт fake repositories."""
        self.analysis_jobs = FakeAnalysisJobRepository()
        self.outbox = FakeOutboxRepository()
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        """Открывает fake transaction."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает fake transaction."""
        del exc_value
        del traceback

        if exc_type is not None:
            await self.rollback()

    async def commit(self) -> None:
        """Фиксирует fake transaction."""
        self.committed = True

    async def rollback(self) -> None:
        """Откатывает fake transaction."""
        self.rolled_back = True


async def test_create_analysis_job_commits_job_and_outbox() -> None:
    """Проверяет атомарное создание job и outbox."""
    unit_of_work = FakeUnitOfWork()

    use_case = CreateAnalysisJob(
        unit_of_work_factory=lambda: unit_of_work,
    )

    document_id = uuid4()

    job = await use_case.execute(
        document_id=document_id,
    )

    assert job.status is AnalysisJobStatus.PENDING
    assert job.document_id == document_id
    assert job.id in unit_of_work.analysis_jobs.jobs

    messages = list(
        unit_of_work.outbox.messages.values(),
    )

    assert len(messages) == 1
    assert messages[0].aggregate_id == job.id
    assert messages[0].payload == {
        "job_id": str(job.id),
    }

    assert unit_of_work.committed is True
    assert unit_of_work.rolled_back is False
