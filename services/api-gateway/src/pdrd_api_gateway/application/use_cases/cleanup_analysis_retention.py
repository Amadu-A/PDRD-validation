# services/api-gateway/src/pdrd_api_gateway/application/use_cases/cleanup_analysis_retention.py

"""Идемпотентная очистка с повторной проверкой владельца под блокировкой строки."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter

from pdrd_api_gateway.application.ports.analysis_retention import (
    AnalysisRetentionArtifacts,
    TechnicalAssignmentRetention,
)
from pdrd_api_gateway.application.ports.persistence import UnitOfWorkFactory
from pdrd_api_gateway.domain.analysis_job import utc_now
from pdrd_api_gateway.domain.analysis_retention import (
    AnalysisRetentionPolicy,
    RetentionAction,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """Количество очищенных заданий и ошибок для журналов и контроля запуска."""

    selected: int = 0
    sources_removed: int = 0
    guests_removed: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class CleanupAnalysisRetention:
    """Файлы удаляются перед фиксацией БД, чтобы частичный сбой можно было повторить."""

    unit_of_work_factory: UnitOfWorkFactory
    artifacts: AnalysisRetentionArtifacts
    policy: AnalysisRetentionPolicy = field(default_factory=AnalysisRetentionPolicy)
    technical_assignments: TechnicalAssignmentRetention | None = None

    async def execute(
        self, *, limit: int = 100, now: datetime | None = None
    ) -> RetentionReport:
        """Ошибку одного задания изолирует; активные и уже очищенные пропускает."""
        if not 1 <= limit <= 1000:
            raise ValueError("Размер пакета очистки должен быть от 1 до 1000.")
        started = perf_counter()
        now = now or utc_now()
        async with self.unit_of_work_factory() as uow:
            candidates = await uow.analysis_jobs.list_retention_candidates(
                source_before=now - self.policy.source_lifetime,
                guest_before=now - self.policy.guest_lifetime,
                limit=limit,
            )
        sources = guests = failed = 0
        for candidate in candidates:
            try:
                async with self.unit_of_work_factory() as uow:
                    job = await uow.analysis_jobs.get_for_update(candidate.id)
                    if job is None:
                        continue
                    action = self.policy.action(job, now=now)
                    if action is RetentionAction.KEEP:
                        continue
                    snapshot = job.normative_snapshot
                    if (
                        snapshot is not None
                        and snapshot.technical_assignment is not None
                        and self.technical_assignments is not None
                    ):
                        (
                            retained,
                            owned,
                        ) = await uow.analysis_jobs.technical_assignment_retention(
                            technical_assignment_id=snapshot.technical_assignment.technical_assignment_id,
                            exclude_job_id=job.id,
                            source_before=now - self.policy.source_lifetime,
                            guest_before=now - self.policy.guest_lifetime,
                        )
                        if not retained:
                            await self.technical_assignments.remove_source(
                                snapshot.technical_assignment,
                                purge_metadata=(
                                    action is RetentionAction.REMOVE_GUEST and not owned
                                ),
                            )
                    if action is RetentionAction.REMOVE_GUEST:
                        await self.artifacts.remove_guest(
                            document_id=job.document_id, job_id=job.id
                        )
                        await uow.analysis_jobs.delete(job.id)
                    else:
                        await self.artifacts.remove_sources(
                            document_id=job.document_id, job_id=job.id
                        )
                        job.source_artifacts_deleted_at = now
                        await uow.analysis_jobs.update(job)
                    await uow.commit()
                    if action is RetentionAction.REMOVE_GUEST:
                        guests += 1
                    else:
                        sources += 1
            except Exception:
                failed += 1
                LOGGER.exception("analysis_retention_failed job_id=%s", candidate.id)
        LOGGER.info(
            "analysis_retention selected=%s sources_removed=%s guests_removed=%s failed=%s duration_ms=%.1f",
            len(candidates),
            sources,
            guests,
            failed,
            (perf_counter() - started) * 1000,
        )
        return RetentionReport(len(candidates), sources, guests, failed)
