# services/api-gateway/tests/unit/test_execute_analysis_job.py

"""Unit tests bounded lifecycle queued analysis job."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.application.ports.orchestration import (
    AnalysisOrchestrationError,
    AnalysisOrchestrationTransientError,
)
from pdrd_api_gateway.application.use_cases.execute_analysis_job import (
    AnalysisExecutionError,
    AnalysisTransientExecutionError,
    ExecuteAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    utc_now,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)


class FakeAnalysisJobRepository:
    """In-memory repository analysis jobs."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
    ) -> None:
        """Сохраняет общее состояние теста."""
        self._state = state

    async def get(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job."""
        return self._state.get(
            job_id,
        )

    async def get_for_update(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Имитирует row lock."""
        return self._state.get(
            job_id,
        )

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет изменённый job."""
        self._state[job.id] = job


class FakeUnitOfWork:
    """Минимальный fake UnitOfWork."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
    ) -> None:
        """Создаёт fake repositories."""
        self.analysis_jobs = FakeAnalysisJobRepository(
            state,
        )

        self.outbox = object()

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

    async def commit(self) -> None:
        """Имитирует commit."""

    async def rollback(self) -> None:
        """Имитирует rollback."""


class FakeUnitOfWorkFactory:
    """Factory fake UnitOfWork."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
    ) -> None:
        """Сохраняет общее repository state."""
        self._state = state

    def __call__(self) -> FakeUnitOfWork:
        """Создаёт transaction."""
        return FakeUnitOfWork(
            self._state,
        )


class FakeArtifactStore:
    """In-memory artifact storage."""

    def __init__(
        self,
        *,
        request: AnalysisRequestArtifacts,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Сохраняет подготовленные данные."""
        self.request = request
        self.result = result

    async def load_request(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisRequestArtifacts:
        """Возвращает исходную заявку."""
        assert document_id == self.request.submission.document_id

        return self.request

    async def save_result(
        self,
        *,
        document_id: UUID,
        result: dict[str, Any],
    ) -> None:
        """Сохраняет итоговый JSON."""
        assert document_id == self.request.submission.document_id

        self.result = result

    async def load_result(
        self,
        *,
        document_id: UUID,
    ) -> dict[str, Any] | None:
        """Возвращает сохранённый result."""
        assert document_id == self.request.submission.document_id

        return self.result


class FakeOrchestrator:
    """Fake n8n orchestration."""

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Подготавливает fake response."""
        self.result = result or {
            "status": "completed",
            "source_mode": "pdf_only",
            "findings_count": 0,
        }

        self.error = error
        self.calls = 0

    async def execute(
        self,
        *,
        artifacts: AnalysisRequestArtifacts,
    ) -> dict[str, Any]:
        """Возвращает result либо бросает ошибку."""
        self.calls += 1

        assert artifacts.pdf_content is not None

        if self.error is not None:
            raise self.error

        return self.result


def build_submission() -> AnalysisRequestArtifacts:
    """Создаёт сохранённую PDF-only заявку."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="1",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
    )

    return AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=b"pdf-content",
        cad_content=None,
    )


def build_use_case(
    *,
    state: dict[UUID, AnalysisJob],
    artifact_store: FakeArtifactStore,
    orchestrator: FakeOrchestrator,
    max_runtime_seconds: int = 1740,
    min_retry_budget_seconds: int = 300,
) -> ExecuteAnalysisJob:
    """Создаёт use case с production-like lifecycle limits."""
    return ExecuteAnalysisJob(
        unit_of_work_factory=FakeUnitOfWorkFactory(
            state,
        ),
        artifact_store=artifact_store,  # type: ignore[arg-type]
        orchestrator=orchestrator,
        max_runtime_seconds=max_runtime_seconds,
        max_attempts=3,
        min_retry_budget_seconds=min_retry_budget_seconds,
    )


@pytest.mark.asyncio
async def test_execute_analysis_job_completes() -> None:
    """Queued job проходит до completed."""
    artifacts = build_submission()

    job = AnalysisJob.create(
        document_id=(artifacts.submission.document_id),
    )

    job.mark_queued()

    state = {
        job.id: job,
    }

    artifact_store = FakeArtifactStore(
        request=artifacts,
    )

    orchestrator = FakeOrchestrator()

    use_case = build_use_case(
        state=state,
        artifact_store=artifact_store,
        orchestrator=orchestrator,
    )

    result = await use_case.execute(
        job_id=job.id,
        allow_retry=True,
        redelivered=False,
    )

    assert result["status"] == "completed"
    assert state[job.id].status is AnalysisJobStatus.COMPLETED
    assert state[job.id].attempt_count == 1
    assert orchestrator.calls == 1
    assert artifact_store.result == result


@pytest.mark.asyncio
async def test_execute_analysis_job_marks_terminal_failure() -> None:
    """Терминальная orchestration ошибка переводит job в failed."""
    artifacts = build_submission()

    job = AnalysisJob.create(
        document_id=(artifacts.submission.document_id),
    )

    job.mark_queued()

    state = {
        job.id: job,
    }

    orchestrator = FakeOrchestrator(
        error=AnalysisOrchestrationError(
            "invalid workflow result",
        ),
    )

    use_case = build_use_case(
        state=state,
        artifact_store=FakeArtifactStore(
            request=artifacts,
        ),
        orchestrator=orchestrator,
    )

    with pytest.raises(
        AnalysisExecutionError,
    ):
        await use_case.execute(
            job_id=job.id,
            allow_retry=True,
            redelivered=False,
        )

    assert state[job.id].status is AnalysisJobStatus.FAILED
    assert state[job.id].error_code == "analysis_execution_failed"
    assert state[job.id].attempt_count == 1


@pytest.mark.asyncio
async def test_transient_failure_returns_job_to_queue() -> None:
    """Короткая infrastructure ошибка допускает controlled retry."""
    artifacts = build_submission()

    job = AnalysisJob.create(
        document_id=(artifacts.submission.document_id),
    )
    job.mark_queued()

    state = {
        job.id: job,
    }

    use_case = build_use_case(
        state=state,
        artifact_store=FakeArtifactStore(
            request=artifacts,
        ),
        orchestrator=FakeOrchestrator(
            error=AnalysisOrchestrationTransientError(
                "EAI_AGAIN",
            ),
        ),
    )

    with pytest.raises(
        AnalysisTransientExecutionError,
    ):
        await use_case.execute(
            job_id=job.id,
            allow_retry=True,
            redelivered=False,
        )

    assert state[job.id].status is AnalysisJobStatus.QUEUED
    assert state[job.id].attempt_count == 1
    assert state[job.id].error_code == "analysis_transient_failure"


@pytest.mark.asyncio
async def test_transient_failure_near_deadline_is_terminal() -> None:
    """Read/transport retry не стартует без достаточного deadline budget."""
    artifacts = build_submission()

    job = AnalysisJob.create(
        document_id=(artifacts.submission.document_id),
    )
    job.created_at = utc_now() - timedelta(
        seconds=1600,
    )
    job.updated_at = job.created_at
    job.mark_queued()

    state = {
        job.id: job,
    }

    use_case = build_use_case(
        state=state,
        artifact_store=FakeArtifactStore(
            request=artifacts,
        ),
        orchestrator=FakeOrchestrator(
            error=AnalysisOrchestrationTransientError(
                "ReadTimeout",
            ),
        ),
    )

    with pytest.raises(
        AnalysisExecutionError,
    ):
        await use_case.execute(
            job_id=job.id,
            allow_retry=True,
            redelivered=False,
        )

    assert state[job.id].status is AnalysisJobStatus.FAILED
    assert state[job.id].error_code == "analysis_transient_retry_exhausted"


@pytest.mark.asyncio
async def test_execute_recovers_existing_result() -> None:
    """Redelivery использует уже сохранённый result.json без второго n8n call."""
    artifacts = build_submission()

    job = AnalysisJob.create(
        document_id=(artifacts.submission.document_id),
    )

    job.mark_queued()
    job.mark_processing()

    state = {
        job.id: job,
    }

    saved_result = {
        "status": "completed",
        "source_mode": "pdf_only",
        "findings_count": 2,
    }

    artifact_store = FakeArtifactStore(
        request=artifacts,
        result=saved_result,
    )

    orchestrator = FakeOrchestrator()

    use_case = build_use_case(
        state=state,
        artifact_store=artifact_store,
        orchestrator=orchestrator,
    )

    result = await use_case.execute(
        job_id=job.id,
        allow_retry=True,
        redelivered=True,
    )

    assert result == saved_result
    assert orchestrator.calls == 0
    assert state[job.id].status is AnalysisJobStatus.COMPLETED
    assert state[job.id].attempt_count == 2


@pytest.mark.asyncio
async def test_job_older_than_absolute_deadline_never_starts_orchestrator() -> None:
    """Просроченная queued задача завершается до внешнего orchestration."""
    artifacts = build_submission()

    job = AnalysisJob.create(
        document_id=(artifacts.submission.document_id),
    )
    job.created_at = utc_now() - timedelta(
        seconds=1801,
    )
    job.updated_at = job.created_at
    job.mark_queued()

    state = {
        job.id: job,
    }

    orchestrator = FakeOrchestrator()

    use_case = build_use_case(
        state=state,
        artifact_store=FakeArtifactStore(
            request=artifacts,
        ),
        orchestrator=orchestrator,
    )

    with pytest.raises(
        AnalysisExecutionError,
    ):
        await use_case.execute(
            job_id=job.id,
            allow_retry=True,
            redelivered=False,
        )

    assert orchestrator.calls == 0
    assert state[job.id].status is AnalysisJobStatus.FAILED
    assert state[job.id].error_code == "analysis_deadline_exceeded"
