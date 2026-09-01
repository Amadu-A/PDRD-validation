# services/api-gateway/tests/unit/test_execute_analysis_job.py

"""Unit tests выполнения queued analysis job."""

from typing import Any
from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.application.ports.orchestration import (
    AnalysisOrchestrationError,
)
from pdrd_api_gateway.application.use_cases.execute_analysis_job import (
    AnalysisExecutionError,
    ExecuteAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
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

    use_case = ExecuteAnalysisJob(
        unit_of_work_factory=FakeUnitOfWorkFactory(
            state,
        ),
        artifact_store=artifact_store,  # type: ignore[arg-type]
        orchestrator=orchestrator,
    )

    result = await use_case.execute(
        job_id=job.id,
    )

    assert result["status"] == "completed"

    assert state[job.id].status is AnalysisJobStatus.COMPLETED

    assert state[job.id].attempt_count == 1

    assert orchestrator.calls == 1

    assert artifact_store.result == result


@pytest.mark.asyncio
async def test_execute_analysis_job_marks_failure() -> None:
    """Ошибка orchestration переводит job в failed."""
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
            "n8n unavailable",
        ),
    )

    use_case = ExecuteAnalysisJob(
        unit_of_work_factory=FakeUnitOfWorkFactory(
            state,
        ),
        artifact_store=FakeArtifactStore(
            request=artifacts,
        ),  # type: ignore[arg-type]
        orchestrator=orchestrator,
    )

    with pytest.raises(
        AnalysisExecutionError,
    ):
        await use_case.execute(
            job_id=job.id,
        )

    assert state[job.id].status is AnalysisJobStatus.FAILED

    assert state[job.id].error_code == "analysis_execution_failed"

    assert state[job.id].attempt_count == 1


@pytest.mark.asyncio
async def test_execute_recovers_existing_result() -> None:
    """Redelivery использует уже сохранённый result.json."""
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

    use_case = ExecuteAnalysisJob(
        unit_of_work_factory=FakeUnitOfWorkFactory(
            state,
        ),
        artifact_store=artifact_store,  # type: ignore[arg-type]
        orchestrator=orchestrator,
    )

    result = await use_case.execute(
        job_id=job.id,
    )

    assert result == saved_result

    assert orchestrator.calls == 0

    assert state[job.id].status is AnalysisJobStatus.COMPLETED

    assert state[job.id].attempt_count == 1
