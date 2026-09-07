# services/knowledge-service/src/pdrd_knowledge_service/application/ports/technical_assignment_persistence.py

"""Persistence ports технических заданий."""

from collections.abc import Callable
from types import TracebackType
from typing import (
    Protocol,
    Self,
)
from uuid import UUID

from pdrd_knowledge_service.application.ports.persistence import (
    NormativeSectionRepository,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignment,
)
from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
)


class TechnicalAssignmentRepository(
    Protocol,
):
    """Persistence lifecycle ТЗ."""

    async def add(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Добавляет ТЗ."""
        ...

    async def get(
        self,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment | None:
        """Возвращает ТЗ."""
        ...

    async def get_for_update(
        self,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment | None:
        """Возвращает ТЗ с PostgreSQL row lock."""
        ...

    async def update(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Обновляет lifecycle metadata."""
        ...


class TechnicalAssignmentOutboxRepository(
    Protocol,
):
    """Persistence отдельного T-outbox."""

    async def add(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Добавляет committed outbox event."""
        ...

    async def get_pending(
        self,
        *,
        limit: int,
    ) -> list[TechnicalAssignmentOutboxMessage]:
        """Возвращает pending events."""
        ...

    async def update(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Обновляет состояние event."""
        ...


class TechnicalAssignmentUnitOfWork(
    Protocol,
):
    """Transaction boundary T lifecycle."""

    sections: NormativeSectionRepository

    assignments: TechnicalAssignmentRepository

    outbox: TechnicalAssignmentOutboxRepository

    async def __aenter__(
        self,
    ) -> Self:
        """Открывает transaction."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает transaction."""
        ...

    async def commit(
        self,
    ) -> None:
        """Commit."""
        ...

    async def rollback(
        self,
    ) -> None:
        """Rollback."""
        ...


TechnicalAssignmentUnitOfWorkFactory = Callable[
    [],
    TechnicalAssignmentUnitOfWork,
]
