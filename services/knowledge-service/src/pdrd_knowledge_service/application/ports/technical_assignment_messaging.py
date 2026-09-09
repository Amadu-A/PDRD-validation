# services/knowledge-service/src/pdrd_knowledge_service/application/ports/technical_assignment_messaging.py

"""Messaging port T-outbox."""

from typing import Protocol

from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
)


class TechnicalAssignmentOutboxPublishError(
    RuntimeError,
):
    """Ошибка публикации T-outbox event."""


class TechnicalAssignmentOutboxPublisher(
    Protocol,
):
    """Контракт publisher T-indexing events."""

    async def publish(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Публикует committed event."""
        ...
