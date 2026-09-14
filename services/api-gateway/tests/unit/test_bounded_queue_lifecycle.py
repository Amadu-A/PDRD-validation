# services/api-gateway/tests/unit/test_bounded_queue_lifecycle.py

"""Unit tests bounded RabbitMQ/Celery lifecycle и stale reconciliation."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from pdrd_api_gateway.application.use_cases.recover_stale_analysis_jobs import (
    RecoverStaleAnalysisJobs,
)
from pdrd_api_gateway.core.settings import BrokerSettings, get_settings
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    utc_now,
)
from pdrd_api_gateway.domain.outbox import OutboxMessage
from pdrd_api_gateway.infrastructure.messaging.celery_app import (
    analysis_queue,
    celery_app,
)
from pdrd_api_gateway.infrastructure.messaging.publisher import CeleryOutboxPublisher


class FakeCelery:
    """Фиксирует параметры send_task без RabbitMQ."""

    def __init__(self) -> None:
        """Создаёт пустой вызов."""
        self.call: dict[str, Any] | None = None

    def send_task(
        self,
        task_name: str,
        **kwargs: Any,
    ) -> None:
        """Сохраняет публикацию."""
        self.call = {
            "task_name": task_name,
            **kwargs,
        }


class FakeJobRepository:
    """Возвращает заранее подготовленные recoverable jobs."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
    ) -> None:
        """Сохраняет состояние."""
        self.state = state

    async def get_recoverable(
        self,
        *,
        stale_processing_before: object,
        deadline_before: object,
        limit: int,
    ) -> list[AnalysisJob]:
        """Возвращает fake batch."""
        del stale_processing_before, deadline_before
        return list(self.state.values())[:limit]

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет job."""
        self.state[job.id] = job


class FakeOutboxRepository:
    """Собирает recovery publications."""

    def __init__(self) -> None:
        """Создаёт пустой список."""
        self.messages: list[OutboxMessage] = []

    async def add(
        self,
        message: OutboxMessage,
    ) -> None:
        """Сохраняет сообщение."""
        self.messages.append(
            message,
        )


class FakeRecoveryUnitOfWork:
    """Fake UoW reconciliation."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
        outbox: FakeOutboxRepository,
    ) -> None:
        """Сохраняет repositories."""
        self.analysis_jobs = FakeJobRepository(
            state,
        )
        self.outbox = outbox

    async def __aenter__(self) -> "FakeRecoveryUnitOfWork":
        """Открывает UoW."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Закрывает UoW."""

    async def commit(self) -> None:
        """Имитирует commit."""

    async def rollback(self) -> None:
        """Имитирует rollback."""


class FakeRecoveryFactory:
    """Factory fake recovery UoW."""

    def __init__(
        self,
        state: dict[UUID, AnalysisJob],
        outbox: FakeOutboxRepository,
    ) -> None:
        """Сохраняет state."""
        self.state = state
        self.outbox = outbox

    def __call__(self) -> FakeRecoveryUnitOfWork:
        """Возвращает UoW."""
        return FakeRecoveryUnitOfWork(
            self.state,
            self.outbox,
        )


def test_analysis_queue_declares_broker_ttl() -> None:
    """Durable queue содержит project-scoped RabbitMQ safety TTL."""
    settings = get_settings()
    arguments = analysis_queue.queue_arguments

    assert arguments is not None
    assert arguments["x-message-ttl"] == settings.broker.message_ttl_seconds * 1000
    assert arguments["x-expires"] == settings.broker.queue_expires_seconds * 1000

    annotation = celery_app.conf.task_annotations["pdrd.analysis.requested"]
    assert (
        annotation["soft_time_limit"] == settings.lifecycle.task_soft_time_limit_seconds
    )
    assert annotation["time_limit"] == settings.lifecycle.task_hard_time_limit_seconds
    assert settings.lifecycle.max_runtime_seconds < 1800
    assert settings.lifecycle.task_hard_time_limit_seconds == 1800


@pytest.mark.asyncio
async def test_outbox_publish_sets_celery_expiration() -> None:
    """Published task получает Celery expires дополнительно к broker TTL."""
    fake_celery = FakeCelery()
    settings = BrokerSettings(
        task_expires_seconds=1234,
    )

    publisher = CeleryOutboxPublisher(
        celery_app=fake_celery,  # type: ignore[arg-type]
        broker_settings=settings,
    )

    message = OutboxMessage.analysis_requested(
        job_id=AnalysisJob.create().id,
    )

    await publisher.publish(
        message,
    )

    assert fake_celery.call is not None
    assert fake_celery.call["expires"] == 1234


@pytest.mark.asyncio
async def test_stale_processing_is_requeued_via_outbox() -> None:
    """Потерянный worker восстанавливается без ручного RabbitMQ purge."""
    now = utc_now()

    job = AnalysisJob.create()
    job.mark_queued()
    job.mark_processing()
    job.updated_at = now - timedelta(
        minutes=5,
    )

    state = {
        job.id: job,
    }
    outbox = FakeOutboxRepository()

    recovery = RecoverStaleAnalysisJobs(
        unit_of_work_factory=FakeRecoveryFactory(
            state,
            outbox,
        ),  # type: ignore[arg-type]
        max_runtime_seconds=1740,
        max_attempts=3,
        stale_processing_seconds=120,
        clock=lambda: now,
    )

    report = await recovery.execute(
        limit=50,
    )

    assert report.selected == 1
    assert report.requeued == 1
    assert report.failed == 0
    assert state[job.id].status is AnalysisJobStatus.QUEUED
    assert len(outbox.messages) == 1
    assert outbox.messages[0].aggregate_id == job.id


@pytest.mark.asyncio
async def test_expired_analysis_is_failed_not_requeued() -> None:
    """Job старше absolute deadline не возвращается в очередь."""
    now = utc_now()

    job = AnalysisJob.create()
    job.created_at = now - timedelta(
        minutes=31,
    )
    job.updated_at = job.created_at
    job.mark_queued()

    state = {
        job.id: job,
    }
    outbox = FakeOutboxRepository()

    recovery = RecoverStaleAnalysisJobs(
        unit_of_work_factory=FakeRecoveryFactory(
            state,
            outbox,
        ),  # type: ignore[arg-type]
        max_runtime_seconds=1740,
        max_attempts=3,
        stale_processing_seconds=120,
        clock=lambda: now,
    )

    report = await recovery.execute(
        limit=50,
    )

    assert report.failed == 1
    assert state[job.id].status is AnalysisJobStatus.FAILED
    assert state[job.id].error_code == "analysis_deadline_exceeded"
    assert outbox.messages == []
