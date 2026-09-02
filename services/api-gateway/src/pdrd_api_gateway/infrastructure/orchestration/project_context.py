# services/api-gateway/src/pdrd_api_gateway/infrastructure/orchestration/project_context.py

"""HTTP adapter страховочного Project Context cleanup."""

from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.project_context import (
    ProjectContextCleanupError,
)
from pdrd_api_gateway.core.settings import (
    ProjectContextCleanupSettings,
)


class KnowledgeProjectContextCleaner:
    """Удаляет Project Context через Knowledge Service."""

    def __init__(
        self,
        *,
        settings: ProjectContextCleanupSettings,
    ) -> None:
        """Сохраняет HTTP settings."""
        self._settings = settings

    async def cleanup(
        self,
        *,
        context_id: UUID,
    ) -> None:
        """Идемпотентно вызывает DELETE Project Context."""
        url = self._settings.base_url.rstrip(
            "/",
        ) + (f"/internal/v1/project-contexts/{context_id}")

        timeout = httpx.Timeout(
            timeout=(self._settings.request_timeout_seconds),
            connect=(self._settings.connect_timeout_seconds),
        )

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.delete(
                    url,
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise ProjectContextCleanupError(
                "Не удалось выполнить "
                "страховочный Project Context cleanup: "
                f"{type(error).__name__}: {error}"
            ) from error
