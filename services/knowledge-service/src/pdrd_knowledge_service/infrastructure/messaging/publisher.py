# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/publisher.py

"""Celery publisher normative transactional outbox."""

import asyncio
from typing import ClassVar

from celery import Celery
from kombu.exceptions import OperationalError

from pdrd_knowledge_service.application.ports.messaging import (
    NormativeOutboxPublishError,
)
from pdrd_knowledge_service.core.settings import (
    BrokerSettings,
)
from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)


class CeleryNormativeOutboxPublisher:
    """Публикует normative outbox events в RabbitMQ."""

    _TASK_BY_EVENT: ClassVar[dict[str, str]] = {
        NormativeOutboxMessage.INDEX_REQUESTED_EVENT: (
            "pdrd.knowledge.normative.index"
        ),
    }

    def __init__(
        self,
        *,
        celery_app: Celery,
        broker_settings: BrokerSettings,
    ) -> None:
        """Сохраняет Celery application и routing settings."""
        self._celery_app = celery_app
        self._broker_settings = broker_settings

    async def publish(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Публикует сообщение вне asyncio event loop."""
        await asyncio.to_thread(
            self._publish_sync,
            message,
        )

    def _publish_sync(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Отправляет task в durable RabbitMQ queue."""
        task_name = self._TASK_BY_EVENT.get(
            message.event_type,
        )

        if task_name is None:
            raise NormativeOutboxPublishError(
                f"Неизвестный тип normative outbox события: {message.event_type}.",
            )

        try:
            self._celery_app.send_task(
                task_name,
                kwargs=message.payload,
                task_id=str(
                    message.message_id,
                ),
                queue=self._broker_settings.queue_name,
                exchange=self._broker_settings.exchange_name,
                routing_key=self._broker_settings.routing_key,
                retry=True,
                ignore_result=True,
            )

        except (
            OSError,
            OperationalError,
        ) as error:
            raise NormativeOutboxPublishError(
                "Не удалось опубликовать normative outbox событие "
                f"{message.message_id}: {type(error).__name__}.",
            ) from error
