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
        project_context_cleaner=(project_context_cleaner),
        technical_assignment_coordinator=(technical_assignment_coordinator),
    )

    try:
        return await use_case.execute(
            job_id=job_id,
        )

    finally:
        await engine.dispose()
