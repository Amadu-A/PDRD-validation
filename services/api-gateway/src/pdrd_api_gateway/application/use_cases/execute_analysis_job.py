# services/api-gateway/src/pdrd_api_gateway/application/use_cases/execute_analysis_job.py

"""Use case выполнения queued analysis job."""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.ports.orchestration import (
    AnalysisOrchestrator,
)
from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.application.ports.project_context import (
    ProjectContextCleaner,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)

LOGGER = logging.getLogger(
    __name__,
)


class AnalysisJobNotFoundError(LookupError):
    """Worker получил неизвестный analysis job."""


class AnalysisJobNotExecutableError(RuntimeError):
    """Analysis job находится в терминальном состоянии."""


class AnalysisExecutionError(RuntimeError):
    """Ошибка фактического выполнения анализа."""


@dataclass(frozen=True, slots=True)
class ExecuteAnalysisJob:
    """Выполняет одно асинхронное задание анализа."""

    unit_of_work_factory: UnitOfWorkFactory

    artifact_store: AnalysisArtifactStore

    orchestrator: AnalysisOrchestrator

    project_context_cleaner: ProjectContextCleaner | None = None

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Запускает job, сохраняет result и очищает Project Context."""
        job = await self._prepare_job(
            job_id=job_id,
        )

        if job.status is AnalysisJobStatus.COMPLETED:
            result = await self._load_completed_result(
                job=job,
            )

            await self._cleanup_project_context(
                context_id=(job.document_id),
            )

            return result

        document_id = job.document_id

        if document_id is None:
            error = RuntimeError(
                "Analysis job не содержит document_id.",
            )

            await self._mark_failed(
                job_id=job_id,
                error=error,
            )

            raise AnalysisExecutionError(
                str(
                    error,
                ),
            ) from error

        try:
            existing_result = await self.artifact_store.load_result(
                document_id=document_id,
            )

            if existing_result is not None:
                await self._mark_completed(
                    job_id=job_id,
                )

                return existing_result

            artifacts = await self.artifact_store.load_request(
                document_id=document_id,
            )

            result = await self.orchestrator.execute(
                artifacts=artifacts,
            )

            await self.artifact_store.save_result(
                document_id=document_id,
                result=result,
            )

        except Exception as error:
            await self._mark_failed(
                job_id=job_id,
                error=error,
            )

            raise AnalysisExecutionError(
                "Не удалось выполнить analysis job "
                f"{job_id}: "
                f"{type(error).__name__}: {error}"
            ) from error

        finally:
            await self._cleanup_project_context(
                context_id=document_id,
            )

        await self._mark_completed(
            job_id=job_id,
        )

        return result

    async def _cleanup_project_context(
        self,
        *,
        context_id: UUID | None,
    ) -> None:
        """Выполняет best-effort cleanup без подмены результата job."""
        if context_id is None or self.project_context_cleaner is None:
            return

        try:
            await self.project_context_cleaner.cleanup(
                context_id=context_id,
            )

        except Exception as error:
            LOGGER.warning(
                "project_context_cleanup_failed context_id=%s error_type=%s error=%s",
                context_id,
                type(
                    error,
                ).__name__,
                error,
            )

    async def _prepare_job(
        self,
        *,
        job_id: UUID,
    ) -> AnalysisJob:
        """Загружает job и переводит его в processing."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get(
                job_id,
            )

            if job is None:
                raise AnalysisJobNotFoundError(
                    f"Analysis job {job_id} не найден.",
                )

            if job.status is AnalysisJobStatus.COMPLETED:
                return job

            if job.status in {
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }:
                raise AnalysisJobNotExecutableError(
                    "Нельзя выполнить analysis job "
                    f"{job_id} в состоянии "
                    f"{job.status.value}.",
                )

            changed = False

            if job.status is AnalysisJobStatus.PENDING:
                job.mark_queued()
                job.mark_processing()

                changed = True

            elif job.status is AnalysisJobStatus.QUEUED:
                job.mark_processing()

                changed = True

            elif job.status is AnalysisJobStatus.PROCESSING:
                pass

            if changed:
                await unit_of_work.analysis_jobs.update(
                    job,
                )

                await unit_of_work.commit()

            return job

    async def _load_completed_result(
        self,
        *,
        job: AnalysisJob,
    ) -> dict[str, Any]:
        """Возвращает результат уже завершённого job."""
        if job.document_id is None:
            raise AnalysisExecutionError(
                "Completed analysis job не содержит document_id.",
            )

        result = await self.artifact_store.load_result(
            document_id=(job.document_id),
        )

        if result is None:
            raise AnalysisExecutionError(
                "Analysis job имеет status=completed, но result.json отсутствует.",
            )

        return result

    async def _mark_completed(
        self,
        *,
        job_id: UUID,
    ) -> None:
        """Фиксирует успешное завершение job."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get(
                job_id,
            )

            if job is None:
                raise AnalysisJobNotFoundError(
                    f"Analysis job {job_id} не найден.",
                )

            if job.status is AnalysisJobStatus.COMPLETED:
                return

            if job.status is not AnalysisJobStatus.PROCESSING:
                raise AnalysisJobNotExecutableError(
                    "Нельзя завершить analysis job "
                    f"{job_id} из состояния "
                    f"{job.status.value}.",
                )

            job.mark_completed()

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()

    async def _mark_failed(
        self,
        *,
        job_id: UUID,
        error: Exception,
    ) -> None:
        """Фиксирует терминальную ошибку выполнения."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get(
                job_id,
            )

            if job is None:
                return

            if job.status in {
                AnalysisJobStatus.COMPLETED,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }:
                return

            job.mark_failed(
                error_code=("analysis_execution_failed"),
                error_message=(f"{type(error).__name__}: {error}")[:2000],
            )

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()
