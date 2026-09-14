# services/api-gateway/src/pdrd_api_gateway/application/use_cases/recover_stale_analysis_jobs.py

"""Reconciliation зависших analysis jobs после crash/redeploy."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pdrd_api_gateway.application.ports.persistence import UnitOfWorkFactory
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
    utc_now,
)
from pdrd_api_gateway.domain.outbox import OutboxMessage

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class AnalysisRecoveryReport:
    """Результат одного reconciliation прохода."""

    selected: int
    requeued: int
    failed: int


@dataclass(frozen=True, slots=True)
class RecoverStaleAnalysisJobs:
    """Восстанавливает stale processing и завершает истёкшие jobs."""

    unit_of_work_factory: UnitOfWorkFactory
    max_runtime_seconds: int
    max_attempts: int
    stale_processing_seconds: int
    clock: Clock = utc_now

    async def execute(
        self,
        *,
        limit: int,
    ) -> AnalysisRecoveryReport:
        """Выполняет bounded reconciliation одной пачки."""
        now = self.clock()

        stale_before = now - timedelta(
            seconds=self.stale_processing_seconds,
        )

        deadline_before = now - timedelta(
            seconds=self.max_runtime_seconds,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            jobs = await unit_of_work.analysis_jobs.get_recoverable(
                stale_processing_before=stale_before,
                deadline_before=deadline_before,
                limit=limit,
            )

            requeued = 0
            failed = 0

            for job in jobs:
                runtime_seconds = (now - job.created_at).total_seconds()

                if runtime_seconds >= self.max_runtime_seconds:
                    job.mark_failed(
                        error_code="analysis_deadline_exceeded",
                        error_message=(
                            "Analysis job превысил абсолютный lifecycle deadline."
                        ),
                    )
                    failed += 1

                elif job.attempt_count >= self.max_attempts:
                    job.mark_failed(
                        error_code="analysis_recovery_exhausted",
                        error_message=(
                            "Исчерпан лимит попыток recovery после worker loss."
                        ),
                    )
                    failed += 1

                elif job.status is AnalysisJobStatus.PROCESSING:
                    job.mark_requeued(
                        error_code="analysis_worker_lost",
                        error_message=(
                            "Heartbeat processing job устарел; создана новая durable delivery."
                        ),
                    )

                    await unit_of_work.outbox.add(
                        OutboxMessage.analysis_requested(
                            job_id=job.id,
                        )
                    )

                    requeued += 1

                await unit_of_work.analysis_jobs.update(
                    job,
                )

            if jobs:
                await unit_of_work.commit()

        return AnalysisRecoveryReport(
            selected=len(jobs),
            requeued=requeued,
            failed=failed,
        )
