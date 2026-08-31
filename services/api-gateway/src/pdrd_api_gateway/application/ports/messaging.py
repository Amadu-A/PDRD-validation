# services/api-gateway/src/pdrd_api_gateway/application/ports/messaging.py

"""Application ports для публикации асинхронных событий."""

from typing import Protocol

from pdrd_api_gateway.domain.outbox import OutboxMessage


class OutboxPublishError(RuntimeError):
    """Ошибка публикации transactional outbox сообщения."""


class OutboxPublisher(Protocol):
    """Контракт внешнего message publisher."""

    async def publish(
        self,
        message: OutboxMessage,
    ) -> None:
        """Публикует одно outbox сообщение."""
        ...
