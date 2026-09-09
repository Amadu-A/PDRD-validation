# services/knowledge-service/src/pdrd_knowledge_service/domain/normative_outbox.py

"""Domain-модель transactional outbox нормативной индексации."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID


@dataclass(slots=True)
class NormativeOutboxMessage:
    """Сообщение durable очереди нормативной индексации."""

    INDEX_REQUESTED_EVENT: ClassVar[str] = "normative.index.requested"

    message_id: UUID

    aggregate_id: UUID

    event_type: str

    payload: dict[str, str]

    attempt_count: int

    last_error: str | None

    created_at: datetime

    published_at: datetime | None

    @classmethod
    def index_requested(
        cls,
        *,
        message_id: UUID,
        document_id: UUID,
        created_at: datetime,
    ) -> "NormativeOutboxMessage":
        """Создаёт событие постановки нормативного документа в индекс."""
        return cls(
            message_id=message_id,
            aggregate_id=document_id,
            event_type=cls.INDEX_REQUESTED_EVENT,
            payload={
                "document_id": str(
                    document_id,
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
        """Фиксирует успешную публикацию в broker."""
        self.attempt_count += 1
        self.last_error = None
        self.published_at = published_at

    def mark_failed(
        self,
        *,
        error_message: str,
    ) -> None:
        """Фиксирует неудачную попытку публикации."""
        self.attempt_count += 1
        self.last_error = error_message[:2000]
