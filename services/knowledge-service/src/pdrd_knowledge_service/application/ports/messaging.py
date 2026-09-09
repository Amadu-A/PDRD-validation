# services/knowledge-service/src/pdrd_knowledge_service/application/ports/messaging.py

"""Application ports публикации Knowledge Service событий."""

from typing import Protocol

from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)


class NormativeOutboxPublishError(RuntimeError):
    """Ошибка публикации normative outbox сообщения."""


class NormativeOutboxPublisher(Protocol):
    """Контракт publisher transactional outbox."""

    async def publish(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Публикует одно committed outbox сообщение."""
        ...
