# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/publisher.py

"""Celery publishers Knowledge transactional outboxes."""

import asyncio
from typing import ClassVar

from celery import Celery
from kombu.exceptions import OperationalError

from pdrd_knowledge_service.application.ports.messaging import (
    NormativeOutboxPublishError,
)
from pdrd_knowledge_service.application.ports.technical_assignment_messaging import (
    TechnicalAssignmentOutboxPublishError,
)
from pdrd_knowledge_service.core.settings import (
    BrokerSettings,
    TechnicalAssignmentQueueSettings,
)
from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)
from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
)


class CeleryNormativeOutboxPublisher:
    """Публикует normative events."""

    _TASK_BY_EVENT: ClassVar[
        dict[
            str,
            str,
        ]
    ] = {
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
        """Сохраняет routing."""
        self._celery_app = celery_app
        self._broker_settings = broker_settings

    async def publish(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Публикует вне asyncio loop."""
        await asyncio.to_thread(
            self._publish_sync,
            message,
        )

    def _publish_sync(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Отправляет normative task."""
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
                expires=self._broker_settings.task_expires_seconds,
                ignore_result=True,
            )

        except (
            OSError,
            OperationalError,
        ) as error:
            raise NormativeOutboxPublishError(
                "Не удалось опубликовать normative outbox "
                f"{message.message_id}: "
                f"{type(error).__name__}.",
            ) from error


class CeleryTechnicalAssignmentOutboxPublisher:
    """Публикует T-indexing events в отдельную очередь."""

    _TASK_BY_EVENT: ClassVar[
        dict[
            str,
            str,
        ]
    ] = {
        (TechnicalAssignmentOutboxMessage.INDEX_REQUESTED_EVENT): (
            "pdrd.knowledge.technical_assignment.index"
        ),
    }

    def __init__(
        self,
        *,
        celery_app: Celery,
        queue_settings: TechnicalAssignmentQueueSettings,
    ) -> None:
        """Сохраняет отдельный T routing."""
        self._celery_app = celery_app
        self._queue_settings = queue_settings

    async def publish(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Публикует вне event loop."""
        await asyncio.to_thread(
            self._publish_sync,
            message,
        )

    def _publish_sync(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Отправляет T task только в T queue."""
        task_name = self._TASK_BY_EVENT.get(
            message.event_type,
        )

        if task_name is None:
            raise TechnicalAssignmentOutboxPublishError(
                f"Неизвестный тип T-outbox события: {message.event_type}.",
            )

        try:
            self._celery_app.send_task(
                task_name,
                kwargs=message.payload,
                task_id=str(
                    message.message_id,
                ),
                queue=self._queue_settings.queue_name,
                exchange=self._queue_settings.exchange_name,
                routing_key=self._queue_settings.routing_key,
                retry=True,
                expires=self._queue_settings.task_expires_seconds,
                ignore_result=True,
            )

        except (
            OSError,
            OperationalError,
        ) as error:
            raise TechnicalAssignmentOutboxPublishError(
                "Не удалось опубликовать T-outbox "
                f"{message.message_id}: "
                f"{type(error).__name__}.",
            ) from error
