# services/api-gateway/tests/unit/test_create_analysis_job.py

"""Unit-тест CreateAnalysisJob без PostgreSQL."""

from types import TracebackType
from uuid import UUID

from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)


class FakeAnalysisJobRepository:
    """In-memory repository для unit-теста."""

    def __init__(self) -> None:
        """Создаёт пустое хранилище."""
        self.jobs: dict[UUID, AnalysisJob] = {}

    async def add(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет job в памяти."""
        self.jobs[job.id] = job

    async def get(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job из памяти."""
        return self.jobs.get(
            job_id,
        )


class FakeUnitOfWork:
    """In-memory Unit of Work для application test."""

    def __init__(self) -> None:
        """Создаёт fake repositories и transaction flags."""
        self.analysis_jobs = FakeAnalysisJobRepository()
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


async def test_create_analysis_job_commits_transaction() -> None:
    """Проверяет сохранение нового задания через Unit of Work."""
    unit_of_work = FakeUnitOfWork()

    use_case = CreateAnalysisJob(
        unit_of_work_factory=lambda: unit_of_work,
    )

    job = await use_case.execute()

    assert job.status is AnalysisJobStatus.PENDING
    assert job.id in unit_of_work.analysis_jobs.jobs
    assert unit_of_work.committed is True
    assert unit_of_work.rolled_back is False
