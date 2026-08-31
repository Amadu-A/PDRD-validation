# services/api-gateway/src/pdrd_api_gateway/application/ports/persistence.py

"""Порты persistence-слоя API Gateway."""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pdrd_api_gateway.domain.analysis_job import AnalysisJob


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


class UnitOfWork(Protocol):
    """Контракт транзакционной границы application operation."""

    analysis_jobs: AnalysisJobRepository

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
