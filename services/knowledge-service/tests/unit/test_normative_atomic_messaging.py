# services/knowledge-service/tests/unit/test_normative_atomic_messaging.py

"""Доставка атомарных заданий через прежнюю очередь без изменения chunk routing."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.ports.messaging import (
    NormativeOutboxPublishError,
)
from pdrd_knowledge_service.core.settings import BrokerSettings
from pdrd_knowledge_service.domain.normative_outbox import NormativeOutboxMessage
from pdrd_knowledge_service.infrastructure.messaging.publisher import (
    CeleryNormativeOutboxPublisher,
)


@pytest.mark.parametrize(
    ("event", "task"),
    [
        (
            NormativeOutboxMessage.INDEX_REQUESTED_EVENT,
            "pdrd.knowledge.normative.index",
        ),
        (
            NormativeOutboxMessage.REQUIREMENTS_REQUESTED_EVENT,
            "pdrd.knowledge.normative_requirements.index",
        ),
    ],
)
async def test_old_and_atomic_outbox_keep_payload_task_identity_and_existing_broker_route(
    event, task
):
    """Доставка сохраняет UUID поколения; новые задания не уходят в ТЗ или новую очередь."""
    settings, send = BrokerSettings(), Mock()
    message = NormativeOutboxMessage(
        message_id=uuid4(),
        aggregate_id=uuid4(),
        event_type=event,
        payload={"document_id": str(uuid4()), "generation_id": str(uuid4())},
        attempt_count=0,
        last_error=None,
        created_at=datetime.now(UTC),
        published_at=None,
    )
    publisher = CeleryNormativeOutboxPublisher(
        celery_app=SimpleNamespace(send_task=send), broker_settings=settings
    )
    await publisher.publish(message)
    assert send.call_args.args == (task,)
    actual = send.call_args.kwargs
    assert actual["kwargs"] == message.payload and actual["task_id"] == str(
        message.message_id
    )
    assert (
        actual["queue"] == settings.queue_name
        and actual["routing_key"] == settings.routing_key
    )
    assert actual["expires"] == settings.task_expires_seconds
    send.side_effect = OSError("broker unavailable")
    with pytest.raises(NormativeOutboxPublishError):
        await publisher.publish(message)
