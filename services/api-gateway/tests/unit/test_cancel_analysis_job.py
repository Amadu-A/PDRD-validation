# services/api-gateway/tests/unit/test_cancel_analysis_job.py

"""Unit tests отмены analysis jobs."""

from uuid import (
    UUID,
    uuid4,
)

import pytest
from pdrd_api_gateway.application.use_cases.cancel_analysis_job import (
    AnalysisJobCancellationConflictError,
    AnalysisJobCancellationNotFoundError,
    CancelAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)


class FakeAnalysisJobRepository:
    """Fake repository с поддержкой row-lock API."""

    def __init__(
        self,
        jobs: dict[
            UUID,
            AnalysisJob,
        ],
    ) -> None:
        """Сохраняет jobs и метрики вызовов."""
        self.jobs = jobs

        self.get_for_update_calls = 0
        self.update_calls = 0

    async def get_for_update(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job как будто под row lock."""
        self.get_for_update_calls += 1

        return self.jobs.get(
            job_id,
        )

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет обновлённый job."""
        self.update_calls += 1

        self.jobs[job.id] = job


class FakeUnitOfWork:
    """Fake transaction boundary."""

    def __init__(
        self,
        repository: FakeAnalysisJobRepository,
    ) -> None:
        """Сохраняет repository и commit counter."""
        self.analysis_jobs = repository

        self.commit_calls = 0

    async def __aenter__(
        self,
    ) -> "FakeUnitOfWork":
        """Открывает fake transaction."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Закрывает fake transaction."""

    async def commit(
        self,
    ) -> None:
        """Фиксирует fake transaction."""
        self.commit_calls += 1

    async def rollback(
        self,
    ) -> None:
        """Имитирует rollback."""


class FakeUnitOfWorkFactory:
    """Возвращает общий fake UoW для одной проверки."""

    def __init__(
        self,
        unit_of_work: FakeUnitOfWork,
    ) -> None:
        """Сохраняет UoW."""
        self.unit_of_work = unit_of_work

    def __call__(
        self,
    ) -> FakeUnitOfWork:
        """Возвращает fake transaction boundary."""
        return self.unit_of_work


def build_use_case(
    job: AnalysisJob | None,
) -> tuple[
    CancelAnalysisJob,
    FakeAnalysisJobRepository,
    FakeUnitOfWork,
]:
    """Собирает CancelAnalysisJob с fake persistence."""
    jobs = (
        {
            job.id: job,
        }
        if job is not None
        else {}
    )

    repository = FakeAnalysisJobRepository(
        jobs,
    )

    unit_of_work = FakeUnitOfWork(
        repository,
    )

    use_case = CancelAnalysisJob(
        unit_of_work_factory=FakeUnitOfWorkFactory(
            unit_of_work,
        ),  # type: ignore[arg-type]
    )

    return (
        use_case,
        repository,
        unit_of_work,
    )


@pytest.mark.parametrize(
    "initial_status",
    [
        AnalysisJobStatus.PENDING,
        AnalysisJobStatus.QUEUED,
        AnalysisJobStatus.PROCESSING,
    ],
)
@pytest.mark.asyncio
async def test_active_analysis_job_can_be_cancelled(
    initial_status: AnalysisJobStatus,
) -> None:
    """Pending, queued и processing jobs переходят в cancelled."""
    job = AnalysisJob.create()

    if initial_status is AnalysisJobStatus.QUEUED:
        job.mark_queued()

    elif initial_status is AnalysisJobStatus.PROCESSING:
        job.mark_queued()
        job.mark_processing()

    use_case, repository, unit_of_work = build_use_case(
        job,
    )

    result = await use_case.execute(
        job_id=job.id,
    )

    assert result is job

    assert result.status is AnalysisJobStatus.CANCELLED

    assert repository.get_for_update_calls == 1
    assert repository.update_calls == 1

    assert unit_of_work.commit_calls == 1


@pytest.mark.asyncio
async def test_cancelled_analysis_job_is_idempotent() -> None:
    """Повторная отмена не создаёт новую запись и commit."""
    job = AnalysisJob.create()
    job.mark_cancelled()

    use_case, repository, unit_of_work = build_use_case(
        job,
    )

    result = await use_case.execute(
        job_id=job.id,
    )

    assert result is job

    assert result.status is AnalysisJobStatus.CANCELLED

    assert repository.get_for_update_calls == 1
    assert repository.update_calls == 0

    assert unit_of_work.commit_calls == 0


@pytest.mark.parametrize(
    "initial_status",
    [
        AnalysisJobStatus.COMPLETED,
        AnalysisJobStatus.FAILED,
    ],
)
@pytest.mark.asyncio
async def test_terminal_analysis_job_cannot_be_cancelled(
    initial_status: AnalysisJobStatus,
) -> None:
    """Completed и failed jobs не переписываются в cancelled."""
    job = AnalysisJob.create()
    job.mark_queued()
    job.mark_processing()

    if initial_status is AnalysisJobStatus.COMPLETED:
        job.mark_completed()

    else:
        job.mark_failed(
            error_code="test_failure",
            error_message="Test failure.",
        )

    use_case, repository, unit_of_work = build_use_case(
        job,
    )

    with pytest.raises(
        AnalysisJobCancellationConflictError,
    ):
        await use_case.execute(
            job_id=job.id,
        )

    assert job.status is initial_status

    assert repository.get_for_update_calls == 1
    assert repository.update_calls == 0

    assert unit_of_work.commit_calls == 0


@pytest.mark.asyncio
async def test_unknown_analysis_job_cannot_be_cancelled() -> None:
    """Для неизвестного job use case возвращает not-found error."""
    use_case, repository, unit_of_work = build_use_case(
        None,
    )

    job_id = uuid4()

    with pytest.raises(
        AnalysisJobCancellationNotFoundError,
    ):
        await use_case.execute(
            job_id=job_id,
        )

    assert repository.get_for_update_calls == 1
    assert repository.update_calls == 0

    assert unit_of_work.commit_calls == 0
