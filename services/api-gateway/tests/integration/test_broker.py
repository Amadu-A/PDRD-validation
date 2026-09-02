# services/api-gateway/tests/integration/test_broker.py

"""Integration-тесты RabbitMQ и Celery worker."""

import os
from uuid import uuid4

import pytest
from pdrd_api_gateway.core.settings import get_settings
from pdrd_api_gateway.infrastructure.messaging.broker import (
    RabbitMqReadinessProbe,
    build_broker_url,
)
from pdrd_api_gateway.infrastructure.messaging.celery_app import (
    celery_app,
)
from pdrd_api_gateway.infrastructure.messaging.tasks import (
    queue_probe,
)

RUN_BROKER_TESTS = (
    os.getenv(
        "PDRD_RUN_BROKER_TESTS",
        "0",
    )
    == "1"
)

pytestmark = pytest.mark.skipif(
    not RUN_BROKER_TESTS,
    reason=("Broker integration tests require PDRD_RUN_BROKER_TESTS=1."),
)


async def test_rabbitmq_is_reachable() -> None:
    """Проверяет реальное AMQP-подключение к project vhost."""
    settings = get_settings()

    probe = RabbitMqReadinessProbe(
        broker_url=build_broker_url(
            settings.broker,
        ),
        connect_timeout_seconds=(settings.broker.connect_timeout_seconds),
        health_timeout_seconds=(settings.broker.health_timeout_seconds),
    )

    assert await probe.is_ready() is True


def test_worker_has_single_concurrency_and_prefetch() -> None:
    """Проверяет ограничения worker, защищающие GPU pipeline."""
    inspector = celery_app.control.inspect(
        timeout=5,
    )

    stats = inspector.stats()

    assert stats

    worker_stats = {
        worker_name: worker_data
        for worker_name, worker_data in stats.items()
        if worker_name.startswith(
            "pdrd-analysis@",
        )
    }

    assert len(worker_stats) == 1

    data = next(
        iter(
            worker_stats.values(),
        )
    )

    assert data["pool"]["max-concurrency"] == 1
    assert data["prefetch_count"] == 1


def test_worker_processes_probe_batch() -> None:
    """Проверяет доставку и выполнение серии сообщений через Celery."""
    probe_ids = [uuid4().hex for _ in range(10)]

    results = [
        queue_probe.apply_async(
            args=[
                probe_id,
            ],
        )
        for probe_id in probe_ids
    ]

    payloads = [
        result.get(
            timeout=15,
        )
        for result in results
    ]

    received_probe_ids = {payload["probe_id"] for payload in payloads}

    assert received_probe_ids == set(
        probe_ids,
    )

    assert {payload["status"] for payload in payloads} == {
        "ok",
    }
