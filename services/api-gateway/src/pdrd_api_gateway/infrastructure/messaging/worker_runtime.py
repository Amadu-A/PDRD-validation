# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/worker_runtime.py

"""Composition root выполнения analysis job внутри Celery worker."""

from functools import partial
from pathlib import Path
from uuid import UUID

from pdrd_api_gateway.application.use_cases.execute_analysis_job import (
    ExecuteAnalysisJob,
)
from pdrd_api_gateway.core.settings import (
    get_settings,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_api_gateway.infrastructure.database.unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from pdrd_api_gateway.infrastructure.knowledge.technical_assignment_index import (
    KnowledgeTechnicalAssignmentIndexCoordinator,
)
from pdrd_api_gateway.infrastructure.orchestration.n8n import (
    N8nAnalysisOrchestrator,
)
from pdrd_api_gateway.infrastructure.orchestration.project_context import (
    KnowledgeProjectContextCleaner,
)
from pdrd_api_gateway.infrastructure.storage.filesystem import (
    LocalFilesystemAnalysisArtifactStore,
)


async def execute_analysis_job(
    *,
    job_id: UUID,
    allow_retry: bool,
    redelivered: bool,
) -> dict[str, object]:
    """Собирает worker dependencies и выполняет один job."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    unit_of_work_factory = partial(
        SqlAlchemyUnitOfWork,
        session_factory,
    )

    artifact_store = LocalFilesystemAnalysisArtifactStore(
        root_path=Path(
            settings.storage.root_path,
        ),
    )

    orchestrator = N8nAnalysisOrchestrator(
        settings=settings.orchestration,
    )

    project_context_cleaner = KnowledgeProjectContextCleaner(
        settings=settings.project_context_cleanup,
    )

    technical_assignment_coordinator = KnowledgeTechnicalAssignmentIndexCoordinator(
        base_url=(settings.knowledge_service.base_url),
        request_timeout_seconds=(settings.knowledge_service.request_timeout_seconds),
        connect_timeout_seconds=(settings.knowledge_service.connect_timeout_seconds),
        wait_timeout_seconds=(settings.technical_assignment.index_wait_timeout_seconds),
        poll_interval_seconds=(
            settings.technical_assignment.index_poll_interval_seconds
        ),
    )

    use_case = ExecuteAnalysisJob(
        unit_of_work_factory=unit_of_work_factory,
        artifact_store=artifact_store,
        orchestrator=orchestrator,
        max_runtime_seconds=settings.lifecycle.max_runtime_seconds,
        max_attempts=settings.lifecycle.max_attempts,
        min_retry_budget_seconds=(settings.lifecycle.min_retry_budget_seconds),
        project_context_cleaner=(project_context_cleaner),
        technical_assignment_coordinator=(technical_assignment_coordinator),
    )

    try:
        return await use_case.execute(
            job_id=job_id,
            allow_retry=allow_retry,
            redelivered=redelivered,
        )

    finally:
        await engine.dispose()


async def touch_analysis_job(
    *,
    job_id: UUID,
) -> None:
    """Обновляет heartbeat processing job отдельной короткой transaction."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    try:
        async with SqlAlchemyUnitOfWork(
            session_factory,
        ) as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_for_update(
                job_id,
            )

            if job is None or job.status is not AnalysisJobStatus.PROCESSING:
                return

            job.touch_processing()

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()

    finally:
        await engine.dispose()


async def fail_analysis_job_from_worker(
    *,
    job_id: UUID,
    error_code: str,
    error_message: str,
) -> None:
    """Best-effort фиксирует worker-level timeout до hard kill."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    try:
        async with SqlAlchemyUnitOfWork(
            session_factory,
        ) as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_for_update(
                job_id,
            )

            if job is None or job.status in {
                AnalysisJobStatus.COMPLETED,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }:
                return

            job.mark_failed(
                error_code=error_code,
                error_message=error_message,
            )

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()

    finally:
        await engine.dispose()
