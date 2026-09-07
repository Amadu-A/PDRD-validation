# services/api-gateway/src/pdrd_api_gateway/application/ports/technical_assignment_index.py

"""Application port T-index readiness barrier."""

from typing import Protocol

from pdrd_api_gateway.domain.technical_assignment import (
    TechnicalAssignmentSnapshot,
)


class TechnicalAssignmentIndexError(
    RuntimeError,
):
    """Ошибка подготовки T index."""


class TechnicalAssignmentIndexCoordinator(
    Protocol,
):
    """Контракт register + wait READY."""

    async def ensure_ready(
        self,
        *,
        snapshot: TechnicalAssignmentSnapshot,
        content: bytes,
    ) -> None:
        """Гарантирует READY до запуска Analysis VLM."""
        ...
