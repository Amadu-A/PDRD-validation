# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/publisher.py

"""Celery implementation transactional outbox publisher."""

import asyncio
from typing import ClassVar

from celery import Celery
from kombu.exceptions import OperationalError

from pdrd_api_gateway.application.ports.messaging import (
    OutboxPublishError,
)
from pdrd_api_gateway.core.settings import BrokerSettings
from pdrd_api_gateway.domain.outbox import OutboxMessage


class CeleryOutboxPublisher:
    """Публикует domain outbox events через Celery/RabbitMQ."""

    _TASK_BY_EVENT: ClassVar[dict[str, str]] = {
        OutboxMessage.ANALYSIS_REQUESTED_EVENT: ("pdrd.analysis.requested"),
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
        message: OutboxMessage,
    ) -> None:
        """Публикует событие, не блокируя asyncio event loop."""
        await asyncio.to_thread(
            self._publish_sync,
            message,
        )

    def _publish_sync(
        self,
        message: OutboxMessage,
    ) -> None:
        """Синхронно отправляет одно сообщение через Celery."""
        task_name = self._TASK_BY_EVENT.get(
            message.event_type,
        )

        if task_name is None:
            raise OutboxPublishError(
                f"Неизвестный тип outbox события: {message.event_type}."
            )

        try:
            self._celery_app.send_task(
                task_name,
                kwargs=message.payload,
                task_id=str(message.id),
                queue=self._broker_settings.queue_name,
                exchange=self._broker_settings.exchange_name,
                routing_key=self._broker_settings.routing_key,
                retry=True,
            )
        except (
            OSError,
            OperationalError,
        ) as error:
            raise OutboxPublishError(
                "Не удалось опубликовать outbox событие "
                f"{message.id}: {type(error).__name__}."
            ) from error
