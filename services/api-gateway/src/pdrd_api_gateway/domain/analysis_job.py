# services/api-gateway/src/pdrd_api_gateway/domain/analysis_job.py

"""Domain-модель асинхронного задания анализа документа."""

from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    UTC,
    datetime,
)
from enum import StrEnum
from typing import ClassVar
from uuid import (
    UUID,
    uuid4,
)

from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)


class AnalysisJobStatus(StrEnum):
    """Допустимые состояния задания анализа."""

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidAnalysisJobTransitionError(ValueError):
    """Ошибка недопустимого перехода между состояниями задания."""


def utc_now() -> datetime:
    """Возвращает текущее время UTC с информацией о timezone."""
    return datetime.now(
        UTC,
    )


@dataclass(slots=True)
class AnalysisJob:
    """Представляет одно пользовательское задание анализа."""

    _ALLOWED_TRANSITIONS: ClassVar[
        dict[
            AnalysisJobStatus,
            frozenset[AnalysisJobStatus],
        ]
    ] = {
        AnalysisJobStatus.PENDING: frozenset(
            {
                AnalysisJobStatus.QUEUED,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }
        ),
        AnalysisJobStatus.QUEUED: frozenset(
            {
                AnalysisJobStatus.PROCESSING,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }
        ),
        AnalysisJobStatus.PROCESSING: frozenset(
            {
                AnalysisJobStatus.COMPLETED,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }
        ),
        AnalysisJobStatus.COMPLETED: frozenset(),
        AnalysisJobStatus.FAILED: frozenset(),
        AnalysisJobStatus.CANCELLED: frozenset(),
    }

    id: UUID

    document_id: UUID | None = None

    normative_snapshot: NormativeAnalysisSnapshot | None = None

    status: AnalysisJobStatus = AnalysisJobStatus.PENDING

    attempt_count: int = 0

    error_code: str | None = None
    error_message: str | None = None

    created_at: datetime = field(
        default_factory=utc_now,
    )

    updated_at: datetime = field(
        default_factory=utc_now,
    )

    @classmethod
    def create(
        cls,
        *,
        document_id: UUID | None = None,
        normative_snapshot: NormativeAnalysisSnapshot | None = None,
    ) -> "AnalysisJob":
        """Создаёт новое задание в состоянии pending."""
        return cls(
            id=uuid4(),
            document_id=document_id,
            normative_snapshot=normative_snapshot,
        )

    def mark_queued(
        self,
    ) -> None:
        """Помечает успешно опубликованное в очереди задание."""
        self._transition_to(
            AnalysisJobStatus.QUEUED,
        )

    def mark_processing(
        self,
    ) -> None:
        """Помечает задание как выполняемое worker-ом."""
        self._transition_to(
            AnalysisJobStatus.PROCESSING,
        )

        self.attempt_count += 1

    def mark_completed(
        self,
    ) -> None:
        """Помечает успешно завершённое задание."""
        self._transition_to(
            AnalysisJobStatus.COMPLETED,
        )

        self.error_code = None
        self.error_message = None

    def mark_failed(
        self,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        """Помечает задание как завершённое с ошибкой."""
        self._transition_to(
            AnalysisJobStatus.FAILED,
        )

        self.error_code = error_code
        self.error_message = error_message

    def mark_cancelled(
        self,
    ) -> None:
        """Помечает отменённое задание."""
        self._transition_to(
            AnalysisJobStatus.CANCELLED,
        )

    def _transition_to(
        self,
        target_status: AnalysisJobStatus,
    ) -> None:
        """Выполняет разрешённый lifecycle transition."""
        allowed_statuses = self._ALLOWED_TRANSITIONS[self.status]

        if target_status not in allowed_statuses:
            raise InvalidAnalysisJobTransitionError(
                "Недопустимый переход задания анализа: "
                f"{self.status.value} -> {target_status.value}.",
            )

        self.status = target_status
        self.updated_at = utc_now()
