# services/api-gateway/tests/unit/test_analysis_lifecycle_load.py

"""Synthetic load tests bounded analysis reconciliation."""

from datetime import timedelta
from uuid import UUID

import pytest
from pdrd_api_gateway.application.use_cases.recover_stale_analysis_jobs import (
    RecoverStaleAnalysisJobs,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    utc_now,
)
from pdrd_api_gateway.domain.outbox import OutboxMessage

_SYNTHETIC_JOB_COUNT = 500


class LoadJobRepository:
    """In-memory repository для synthetic lifecycle load."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
    ) -> None:
        """Сохраняет jobs."""
        self.state = state

    async def get_recoverable(
        self,
        *,
        stale_processing_before: object,
        deadline_before: object,
        limit: int,
    ) -> list[AnalysisJob]:
        """Возвращает bounded batch."""
        del stale_processing_before, deadline_before
        return list(self.state.values())[:limit]

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет lifecycle result."""
        self.state[job.id] = job


class LoadOutboxRepository:
    """Считает synthetic requeue events."""

    def __init__(self) -> None:
        """Создаёт пустой event list."""
        self.messages: list[OutboxMessage] = []

    async def add(
        self,
        message: OutboxMessage,
    ) -> None:
        """Сохраняет event."""
        self.messages.append(
            message,
        )


class LoadUnitOfWork:
    """UoW для synthetic batch."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
        outbox: LoadOutboxRepository,
    ) -> None:
        """Создаёт repositories."""
        self.analysis_jobs = LoadJobRepository(
            state,
        )
        self.outbox = outbox

    async def __aenter__(self) -> "LoadUnitOfWork":
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
        """Имитирует один batch commit."""

    async def rollback(self) -> None:
        """Имитирует rollback."""


class LoadUnitOfWorkFactory:
    """Factory synthetic UoW."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
        outbox: LoadOutboxRepository,
    ) -> None:
        """Сохраняет общие данные."""
        self.state = state
        self.outbox = outbox

    def __call__(self) -> LoadUnitOfWork:
        """Возвращает synthetic UoW."""
        return LoadUnitOfWork(
            self.state,
            self.outbox,
        )


@pytest.mark.asyncio
async def test_recovery_handles_500_synthetic_stale_jobs_losslessly() -> None:
    """500 stale jobs дают 500 независимых durable requeue events."""
    now = utc_now()

    jobs: dict[UUID, AnalysisJob] = {}

    for _ in range(
        _SYNTHETIC_JOB_COUNT,
    ):
        job = AnalysisJob.create()
        job.mark_queued()
        job.mark_processing()
        job.updated_at = now - timedelta(
            minutes=5,
        )
        jobs[job.id] = job

    outbox = LoadOutboxRepository()

    recovery = RecoverStaleAnalysisJobs(
        unit_of_work_factory=LoadUnitOfWorkFactory(
            jobs,
            outbox,
        ),  # type: ignore[arg-type]
        max_runtime_seconds=1740,
        max_attempts=3,
        stale_processing_seconds=120,
        clock=lambda: now,
    )

    report = await recovery.execute(
        limit=_SYNTHETIC_JOB_COUNT,
    )

    assert report.selected == _SYNTHETIC_JOB_COUNT
    assert report.requeued == _SYNTHETIC_JOB_COUNT
    assert report.failed == 0
    assert len(outbox.messages) == _SYNTHETIC_JOB_COUNT
    assert len({message.id for message in outbox.messages}) == _SYNTHETIC_JOB_COUNT
    assert all(job.status is AnalysisJobStatus.QUEUED for job in jobs.values())
