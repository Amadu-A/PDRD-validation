# services/api-gateway/src/pdrd_api_gateway/application/ports/technical_assignment_index.py

"""Application port T-index lifecycle."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pdrd_api_gateway.domain.technical_assignment import (
    TechnicalAssignmentSnapshot,
)


class TechnicalAssignmentIndexError(
    RuntimeError,
):
    """Ошибка подготовки T index."""


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentIndexState:
    """Текущее состояние индексации ТЗ."""

    technical_assignment_id: UUID

    index_status: str

    index_error: str | None


class TechnicalAssignmentIndexCoordinator(
    Protocol,
):
    """Контракт register/status/READY lifecycle."""

    async def register(
        self,
        *,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
    ) -> TechnicalAssignmentIndexState:
        """Регистрирует ТЗ и запускает background indexing."""
        ...

    async def get_status(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignmentIndexState:
        """Возвращает актуальный статус ТЗ."""
        ...

    async def ensure_ready(
        self,
        *,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
    ) -> None:
        """Гарантирует READY до запуска Analysis VLM."""
        ...
