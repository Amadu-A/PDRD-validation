# services/api-gateway/src/pdrd_api_gateway/application/ports/persistence.py

"""Порты persistence-слоя API Gateway."""

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.domain.outbox import OutboxMessage


class AnalysisJobRepository(Protocol):
    """Контракт persistence операций над заданиями анализа."""

    async def add(
        self,
        job: AnalysisJob,
    ) -> None:
        """Добавляет новое задание в текущую транзакцию."""
        ...

    async def get(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает задание по идентификатору."""
        ...

    async def list_by_owner(
        self, *, owner_user_id: UUID, limit: int, offset: int
    ) -> list[AnalysisJob]:
        """Возвращает только задания владельца, новые раньше старых, с устойчивым порядком."""
        ...

    async def get_for_update(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job с PostgreSQL row lock."""
        ...

    async def get_by_document_id_for_update(
        self,
        document_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job по document_id с PostgreSQL row lock."""
        ...

    async def list_retention_candidates(
        self,
        *,
        source_before: datetime,
        guest_before: datetime,
        limit: int,
    ) -> list[AnalysisJob]:
        """Выбирает завершённые задания с истёкшим сроком хранения."""
        ...

    async def technical_assignment_retention(
        self,
        *,
        technical_assignment_id: UUID,
        exclude_job_id: UUID,
        source_before: datetime,
        guest_before: datetime,
    ) -> tuple[bool, bool]:
        """Возвращает наличие свежих/активных ссылок и бессрочных ссылок владельца."""
        ...

    async def delete(self, job_id: UUID) -> None:
        """Удаляет гостевую строку и связанные исходящие события."""
        ...

    async def count_waiting_before(
        self,
        *,
        created_at: datetime,
        job_id: UUID,
    ) -> int:
        """Считает pending/queued jobs раньше указанного задания."""
        ...

    async def get_recoverable(
        self,
        *,
        stale_processing_before: datetime,
        deadline_before: datetime,
        limit: int,
    ) -> list[AnalysisJob]:
        """Возвращает stale processing и просроченные active jobs."""
        ...

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Обновляет существующее задание."""
        ...


class OutboxRepository(Protocol):
    """Контракт transactional outbox repository."""

    async def add(
        self,
        message: OutboxMessage,
    ) -> None:
        """Добавляет сообщение в текущую транзакцию."""
        ...

    async def get_pending(
        self,
        *,
        limit: int,
    ) -> list[OutboxMessage]:
        """Возвращает ожидающие публикации сообщения."""
        ...

    async def update(
        self,
        message: OutboxMessage,
    ) -> None:
        """Обновляет состояние outbox сообщения."""
        ...


class UnitOfWork(Protocol):
    """Контракт транзакционной границы application operation."""

    analysis_jobs: AnalysisJobRepository
    outbox: OutboxRepository

    async def __aenter__(self) -> Self:
        """Открывает транзакционную область."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает транзакционную область."""
        ...

    async def commit(self) -> None:
        """Фиксирует текущую транзакцию."""
        ...

    async def rollback(self) -> None:
        """Откатывает текущую транзакцию."""
        ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
