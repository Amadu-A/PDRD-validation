# services/api-gateway/src/pdrd_api_gateway/domain/analysis_retention.py

"""Правила хранения результатов владельца и временных гостевых проверок."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pdrd_api_gateway.domain.analysis_job import AnalysisJob, AnalysisJobStatus


class RetentionAction(StrEnum):
    """Допустимые действия после завершения задания и истечения срока."""

    KEEP = "keep"
    REMOVE_SOURCES = "remove_sources"
    REMOVE_GUEST = "remove_guest"


@dataclass(frozen=True, slots=True)
class AnalysisRetentionPolicy:
    """Отсчитывает 30/7 дней от создания; активные задания всегда сохраняет."""

    source_lifetime: timedelta = timedelta(days=30)
    guest_lifetime: timedelta = timedelta(days=7)

    def action(self, job: AnalysisJob, *, now: datetime) -> RetentionAction:
        """Никогда не удаляет результат задания с владельцем."""
        if now.tzinfo is None or job.created_at.tzinfo is None:
            raise ValueError("Срок хранения требует время с часовым поясом.")
        if job.status not in {
            AnalysisJobStatus.COMPLETED,
            AnalysisJobStatus.FAILED,
            AnalysisJobStatus.CANCELLED,
        }:
            return RetentionAction.KEEP
        if job.owner_user_id is None:
            return (
                RetentionAction.REMOVE_GUEST
                if now >= job.created_at + self.guest_lifetime
                else RetentionAction.KEEP
            )
        if (
            job.source_artifacts_deleted_at is None
            and now >= job.created_at + self.source_lifetime
        ):
            return RetentionAction.REMOVE_SOURCES
        return RetentionAction.KEEP
