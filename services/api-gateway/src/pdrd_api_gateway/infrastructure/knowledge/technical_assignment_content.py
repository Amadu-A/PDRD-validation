# services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/technical_assignment_content.py

"""HTTP adapter чтения T PDF-preview."""

from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.technical_assignment_content import (
    TechnicalAssignmentContent,
    TechnicalAssignmentContentError,
    TechnicalAssignmentContentNotFoundError,
)
from pdrd_api_gateway.core.settings import (
    KnowledgeServiceSettings,
)


class HttpTechnicalAssignmentContentReader:
    """Читает T-content через Knowledge Service."""

    def __init__(
        self,
        *,
        settings: KnowledgeServiceSettings,
    ) -> None:
        """Сохраняет HTTP settings."""
        self._base_url = settings.base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = settings.request_timeout_seconds

        self._connect_timeout_seconds = settings.connect_timeout_seconds

    async def get_content(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignmentContent:
        """Проксирует internal T content."""
        timeout = httpx.Timeout(
            self._request_timeout_seconds,
            connect=self._connect_timeout_seconds,
        )

        url = (
            f"{self._base_url}"
            "/internal/v1/technical-assignments/"
            f"{technical_assignment_id}/content"
        )

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.get(
                    url,
                )

        except httpx.HTTPError as error:
            raise TechnicalAssignmentContentError(
                "Knowledge Service недоступен при чтении ТЗ.",
            ) from error

        if response.status_code == 404:
            raise TechnicalAssignmentContentNotFoundError(
                f"ТЗ {technical_assignment_id} не найдено.",
            )

        try:
            response.raise_for_status()

        except httpx.HTTPStatusError as error:
            raise TechnicalAssignmentContentError(
                "Knowledge Service не вернул T-content: "
                f"{response.status_code}: "
                f"{response.text[:1000]}",
            ) from error

        mime_type = (
            response.headers.get(
                "content-type",
                "application/pdf",
            )
            .split(
                ";",
                maxsplit=1,
            )[0]
            .strip()
        )

        return TechnicalAssignmentContent(
            content=response.content,
            mime_type=mime_type,
        )
