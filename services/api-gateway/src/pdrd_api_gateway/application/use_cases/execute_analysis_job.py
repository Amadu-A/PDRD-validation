# services/api-gateway/src/pdrd_api_gateway/application/use_cases/execute_analysis_job.py

"""Use case выполнения queued analysis job."""

import asyncio
import logging
from dataclasses import (
    dataclass,
    replace,
)
from datetime import datetime
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.ports.orchestration import (
    AnalysisOrchestrationTransientError,
    AnalysisOrchestrator,
)
from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.application.ports.project_context import (
    ProjectContextCleaner,
)
from pdrd_api_gateway.application.ports.technical_assignment_index import (
    TechnicalAssignmentIndexCoordinator,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    utc_now,
)

LOGGER = logging.getLogger(
    __name__,
)


class AnalysisJobNotFoundError(
    LookupError,
):
    """Worker получил неизвестный analysis job."""


class AnalysisJobNotExecutableError(
    RuntimeError,
):
    """Analysis job находится в terminal state."""


class AnalysisExecutionError(
    RuntimeError,
):
    """Ошибка фактического выполнения анализа."""


class AnalysisTransientExecutionError(
    RuntimeError,
):
    """Временная ошибка, для которой разрешён controlled Celery retry."""


@dataclass(frozen=True, slots=True)
class ExecuteAnalysisJob:
    """Выполняет одно asynchronous analysis job."""

    unit_of_work_factory: UnitOfWorkFactory

    artifact_store: AnalysisArtifactStore

    orchestrator: AnalysisOrchestrator

    max_runtime_seconds: int

    max_attempts: int

    min_retry_budget_seconds: int

    project_context_cleaner: ProjectContextCleaner | None = None

    technical_assignment_coordinator: TechnicalAssignmentIndexCoordinator | None = None

    async def execute(
        self,
        *,
        job_id: UUID,
        allow_retry: bool,
        redelivered: bool,
    ) -> dict[
        str,
        Any,
    ]:
        """Запускает job с абсолютным deadline и idempotent redelivery."""
        job = await self._prepare_job(
            job_id=job_id,
            redelivered=redelivered,
        )

        if job.status is AnalysisJobStatus.COMPLETED:
            result = await self._load_completed_result(
                job=job,
            )

            await self._cleanup_project_context(
                context_id=job.document_id,
            )

            return result

        if job.attempt_count > self.max_attempts:
            await self._mark_failed(
                job_id=job_id,
                error=RuntimeError(
                    "Исчерпан лимит analysis attempts.",
                ),
                error_code="analysis_attempts_exhausted",
            )

            raise AnalysisExecutionError(
                f"Analysis job {job_id} превысил max_attempts.",
            )

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
                )
            ) from error

        remaining_seconds = self._remaining_seconds(
            job=job,
        )

        if remaining_seconds <= 0:
            await self._mark_failed(
                job_id=job_id,
                error=TimeoutError(
                    "Истёк абсолютный deadline analysis job.",
                ),
                error_code="analysis_deadline_exceeded",
            )

            raise AnalysisExecutionError(
                f"Analysis job {job_id} уже превысил lifecycle deadline.",
            )

        try:
            async with asyncio.timeout(
                remaining_seconds,
            ):
                result = await self._execute_pipeline(
                    job=job,
                    document_id=document_id,
                )

        except TimeoutError as error:
            await self._mark_failed(
                job_id=job_id,
                error=error,
                error_code="analysis_deadline_exceeded",
            )

            raise AnalysisExecutionError(
                "Analysis job превысил абсолютный lifecycle deadline "
                f"{self.max_runtime_seconds} секунд.",
            ) from error

        except AnalysisOrchestrationTransientError as error:
            if self._can_retry(
                job=job,
                allow_retry=allow_retry,
            ):
                await self._mark_requeued(
                    job_id=job_id,
                    error=error,
                )

                raise AnalysisTransientExecutionError(
                    "Transient orchestration error допускает controlled retry: "
                    f"{type(error).__name__}: {error}",
                ) from error

            await self._mark_failed(
                job_id=job_id,
                error=error,
                error_code="analysis_transient_retry_exhausted",
            )

            raise AnalysisExecutionError(
                "Transient orchestration error не может быть повторена "
                "в оставшемся deadline budget.",
            ) from error

        except Exception as error:
            await self._mark_failed(
                job_id=job_id,
                error=error,
            )

            raise AnalysisExecutionError(
                "Не удалось выполнить analysis job "
                f"{job_id}: "
                f"{type(error).__name__}: {error}",
            ) from error

        finally:
            await self._cleanup_project_context(
                context_id=document_id,
            )

        await self._mark_completed(
            job_id=job_id,
        )

        return result

    async def _execute_pipeline(
        self,
        *,
        job: AnalysisJob,
        document_id: UUID,
    ) -> dict[str, Any]:
        """Выполняет idempotent pipeline внутри lifecycle deadline."""
        existing_result = await self.artifact_store.load_result(
            document_id=document_id,
        )

        if existing_result is not None:
            return existing_result

        artifacts = await self.artifact_store.load_request(
            document_id=document_id,
        )

        await self._ensure_technical_assignment_ready(
            job=job,
            document_id=document_id,
        )

        artifacts = replace(
            artifacts,
            normative_snapshot=job.normative_snapshot,
        )

        result = await self.orchestrator.execute(
            artifacts=artifacts,
        )

        await self.artifact_store.save_result(
            document_id=document_id,
            result=result,
        )

        return result

    def _remaining_seconds(
        self,
        *,
        job: AnalysisJob,
        now: datetime | None = None,
    ) -> float:
        """Возвращает остаток абсолютного lifecycle budget."""
        current = now or utc_now()

        elapsed = (current - job.created_at).total_seconds()

        return max(
            float(self.max_runtime_seconds) - elapsed,
            0.0,
        )

    def _can_retry(
        self,
        *,
        job: AnalysisJob,
        allow_retry: bool,
    ) -> bool:
        """Разрешает retry только при достаточном времени и attempts budget."""
        if not allow_retry:
            return False

        if job.attempt_count >= self.max_attempts:
            return False

        return (
            self._remaining_seconds(
                job=job,
            )
            >= self.min_retry_budget_seconds
        )

    async def _ensure_technical_assignment_ready(
        self,
        *,
        job: AnalysisJob,
        document_id: UUID,
    ) -> None:
        """Не разрешает n8n до READY T-index."""
        snapshot = job.normative_snapshot

        if snapshot is None or snapshot.technical_assignment is None:
            return

        coordinator = self.technical_assignment_coordinator

        if coordinator is None:
            raise RuntimeError(
                "Analysis job содержит ТЗ, но T-index coordinator не настроен.",
            )

        content = await self.artifact_store.load_technical_assignment(
            document_id=document_id,
        )

        if content is None:
            raise RuntimeError(
                "Immutable snapshot содержит ТЗ, но physical artifact отсутствует.",
            )

        await coordinator.ensure_ready(
            snapshot=snapshot.technical_assignment,
            content=content,
        )

    async def _cleanup_project_context(
        self,
        *,
        context_id: UUID | None,
    ) -> None:
        """Best-effort cleanup Project Context."""
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
        redelivered: bool,
    ) -> AnalysisJob:
        """Загружает job под row lock и переводит в processing."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_for_update(
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

            elif job.status is AnalysisJobStatus.PROCESSING and redelivered:
                job.resume_processing_attempt()
                changed = True

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
    ) -> dict[
        str,
        Any,
    ]:
        """Возвращает result уже completed job."""
        if job.document_id is None:
            raise AnalysisExecutionError(
                "Completed analysis job не содержит document_id.",
            )

        result = await self.artifact_store.load_result(
            document_id=job.document_id,
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
        """Фиксирует успешное завершение."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_for_update(
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

    async def _mark_requeued(
        self,
        *,
        job_id: UUID,
        error: Exception,
    ) -> None:
        """Возвращает transient failure в queued перед Celery retry."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_for_update(
                job_id,
            )

            if job is None:
                return

            if job.status is not AnalysisJobStatus.PROCESSING:
                return

            job.mark_requeued(
                error_code="analysis_transient_failure",
                error_message=(f"{type(error).__name__}: {error}"),
            )

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()

    async def _mark_failed(
        self,
        *,
        job_id: UUID,
        error: Exception,
        error_code: str = "analysis_execution_failed",
    ) -> None:
        """Фиксирует terminal execution error."""
        async with self.unit_of_work_factory() as unit_of_work:
            job = await unit_of_work.analysis_jobs.get_for_update(
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
                error_code=error_code,
                error_message=(f"{type(error).__name__}: {error}"),
            )

            await unit_of_work.analysis_jobs.update(
                job,
            )

            await unit_of_work.commit()
