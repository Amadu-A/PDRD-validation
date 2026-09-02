# services/api-gateway/src/pdrd_api_gateway/application/ports/project_context.py

"""Application port cleanup временного Project Context."""

from typing import Protocol
from uuid import UUID


class ProjectContextCleanupError(RuntimeError):
    """Ошибка cleanup временного Project Context."""


class ProjectContextCleaner(Protocol):
    """Контракт идемпотентного Project Context cleanup."""

    async def cleanup(
        self,
        *,
        context_id: UUID,
    ) -> None:
        """Удаляет временный context по document UUID."""
        ...
