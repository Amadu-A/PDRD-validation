# services/api-gateway/tests/unit/test_update_analysis_progress.py

"""Unit tests durable analysis progress checkpoints."""

from uuid import (
    UUID,
    uuid4,
)

import pytest
from pdrd_api_gateway.application.use_cases.update_analysis_progress import (
    AnalysisProgressJobNotFoundError,
    UpdateAnalysisProgress,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisProgressStage,
)


class FakeAnalysisJobRepository:
    """In-memory repository для progress tests."""

    def __init__(
        self,
        *,
        jobs: dict[UUID, AnalysisJob],
        counters: dict[str, int],
    ) -> None:
        """Сохраняет jobs и общие counters."""
        self._jobs = jobs
        self._counters = counters

    async def get_by_document_id_for_update(
        self,
        document_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job по document_id."""
        return self._jobs.get(
            document_id,
        )

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Имитирует сохранение job."""
        document_id = job.document_id

        assert document_id is not None

        self._jobs[document_id] = job
        self._counters["updates"] += 1


class FakeUnitOfWork:
    """Минимальный UnitOfWork для progress use case."""

    def __init__(
        self,
        *,
        jobs: dict[UUID, AnalysisJob],
        counters: dict[str, int],
    ) -> None:
        """Создаёт fake repository."""
        self.analysis_jobs = FakeAnalysisJobRepository(
            jobs=jobs,
            counters=counters,
        )

        self._counters = counters

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
        """Имитирует commit."""
        self._counters["commits"] += 1

    async def rollback(
        self,
    ) -> None:
        """Имитирует rollback."""


class FakeUnitOfWorkFactory:
    """Factory UnitOfWork для progress tests."""

    def __init__(
        self,
        *,
        jobs: dict[UUID, AnalysisJob],
    ) -> None:
        """Сохраняет shared state."""
        self.jobs = jobs
        self.counters = {
            "updates": 0,
            "commits": 0,
        }

    def __call__(
        self,
    ) -> FakeUnitOfWork:
        """Создаёт fake transaction."""
        return FakeUnitOfWork(
            jobs=self.jobs,
            counters=self.counters,
        )


def build_processing_job() -> AnalysisJob:
    """Создаёт processing analysis job с document_id."""
    job = AnalysisJob.create(
        document_id=uuid4(),
    )

    job.mark_queued()
    job.mark_processing()

    return job


@pytest.mark.asyncio
async def test_progress_checkpoint_updates_processing_job() -> None:
    """Новый monotonic stage сохраняется и разрешает workflow продолжиться."""
    job = build_processing_job()

    assert job.document_id is not None

    factory = FakeUnitOfWorkFactory(
        jobs={
            job.document_id: job,
        },
    )

    use_case = UpdateAnalysisProgress(
        unit_of_work_factory=factory,  # type: ignore[arg-type]
    )

    result = await use_case.execute(
        document_id=job.document_id,
        stage=AnalysisProgressStage.EXTRACTING_SOURCES,
    )

    assert result.changed is True
    assert result.cancelled is False
    assert job.progress_stage is AnalysisProgressStage.EXTRACTING_SOURCES
    assert factory.counters["updates"] == 1
    assert factory.counters["commits"] == 1


@pytest.mark.asyncio
async def test_repeated_progress_checkpoint_refreshes_processing_job() -> None:
    """Повторный stage обновляет durable heartbeat и продолжает workflow."""
    job = build_processing_job()

    assert job.document_id is not None

    job.update_progress(
        stage=AnalysisProgressStage.PREPARING_CONTEXT,
    )

    factory = FakeUnitOfWorkFactory(
        jobs={
            job.document_id: job,
        },
    )

    use_case = UpdateAnalysisProgress(
        unit_of_work_factory=factory,  # type: ignore[arg-type]
    )

    result = await use_case.execute(
        document_id=job.document_id,
        stage=AnalysisProgressStage.PREPARING_CONTEXT,
    )

    assert result.changed is True
    assert result.cancelled is False
    assert job.progress_stage is AnalysisProgressStage.PREPARING_CONTEXT
    assert factory.counters["updates"] == 1
    assert factory.counters["commits"] == 1


@pytest.mark.asyncio
async def test_cancelled_job_returns_explicit_cancellation_signal() -> None:
    """Cancelled job блокирует следующий orchestration stage."""
    job = build_processing_job()

    assert job.document_id is not None

    job.update_progress(
        stage=AnalysisProgressStage.UNDERSTANDING_SHEET,
    )
    job.mark_cancelled()

    factory = FakeUnitOfWorkFactory(
        jobs={
            job.document_id: job,
        },
    )

    use_case = UpdateAnalysisProgress(
        unit_of_work_factory=factory,  # type: ignore[arg-type]
    )

    result = await use_case.execute(
        document_id=job.document_id,
        stage=AnalysisProgressStage.RETRIEVING_REQUIREMENTS,
    )

    assert result.changed is False
    assert result.cancelled is True
    assert job.status is AnalysisJobStatus.CANCELLED
    assert job.progress_stage is AnalysisProgressStage.UNDERSTANDING_SHEET
    assert factory.counters["updates"] == 0
    assert factory.counters["commits"] == 0


@pytest.mark.asyncio
async def test_unknown_document_id_is_rejected() -> None:
    """Неизвестный document_id по-прежнему является ошибкой callback contract."""
    factory = FakeUnitOfWorkFactory(
        jobs={},
    )

    use_case = UpdateAnalysisProgress(
        unit_of_work_factory=factory,  # type: ignore[arg-type]
    )

    with pytest.raises(
        AnalysisProgressJobNotFoundError,
    ):
        await use_case.execute(
            document_id=uuid4(),
            stage=AnalysisProgressStage.EXTRACTING_SOURCES,
        )

    assert factory.counters["updates"] == 0
    assert factory.counters["commits"] == 0
