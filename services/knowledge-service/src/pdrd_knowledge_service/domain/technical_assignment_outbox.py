# services/knowledge-service/src/pdrd_knowledge_service/domain/technical_assignment_outbox.py

"""Transactional outbox технического задания."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID


@dataclass(slots=True)
class TechnicalAssignmentOutboxMessage:
    """Durable T-indexing event."""

    INDEX_REQUESTED_EVENT: ClassVar[str] = "technical_assignment.index.requested"

    message_id: UUID

    aggregate_id: UUID

    event_type: str

    payload: dict[
        str,
        str,
    ]

    attempt_count: int

    last_error: str | None

    created_at: datetime

    published_at: datetime | None

    @classmethod
    def index_requested(
        cls,
        *,
        message_id: UUID,
        technical_assignment_id: UUID,
        created_at: datetime,
    ) -> "TechnicalAssignmentOutboxMessage":
        """Создаёт durable request индексации ТЗ."""
        return cls(
            message_id=message_id,
            aggregate_id=technical_assignment_id,
            event_type=cls.INDEX_REQUESTED_EVENT,
            payload={
                "technical_assignment_id": str(
                    technical_assignment_id,
                ),
            },
            attempt_count=0,
            last_error=None,
            created_at=created_at,
            published_at=None,
        )

    def mark_published(
        self,
        *,
        published_at: datetime,
    ) -> None:
        """Фиксирует успешную broker publication."""
        self.attempt_count += 1

        self.last_error = None

        self.published_at = published_at

    def mark_failed(
        self,
        *,
        error_message: str,
    ) -> None:
        """Фиксирует неудачную публикацию."""
        self.attempt_count += 1

        self.last_error = error_message[:2000]
