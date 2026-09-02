# services/api-gateway/src/pdrd_api_gateway/domain/outbox.py

"""Domain-модель transactional outbox."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from pdrd_api_gateway.domain.analysis_job import utc_now


@dataclass(slots=True)
class OutboxMessage:
    """Сообщение, ожидающее надёжной публикации в message broker."""

    ANALYSIS_REQUESTED_EVENT: ClassVar[str] = "analysis.requested"

    id: UUID
    aggregate_id: UUID
    event_type: str
    payload: dict[str, str]

    attempt_count: int = 0
    last_error: str | None = None

    created_at: datetime = field(
        default_factory=utc_now,
    )

    published_at: datetime | None = None

    @classmethod
    def analysis_requested(
        cls,
        *,
        job_id: UUID,
    ) -> "OutboxMessage":
        """Создаёт сообщение о новом задании анализа."""
        return cls(
            id=uuid4(),
            aggregate_id=job_id,
            event_type=cls.ANALYSIS_REQUESTED_EVENT,
            payload={
                "job_id": str(job_id),
            },
        )

    def mark_published(self) -> None:
        """Фиксирует успешную публикацию события."""
        self.attempt_count += 1
        self.last_error = None
        self.published_at = utc_now()

    def mark_failed(
        self,
        *,
        error_message: str,
    ) -> None:
        """Фиксирует неудачную попытку публикации."""
        self.attempt_count += 1
        self.last_error = error_message[:2000]
