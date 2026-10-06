# services/api-gateway/src/pdrd_api_gateway/infrastructure/pdf_selection.py

"""HTTP-проверка выбора страниц в сервисе, владеющем обработкой PDF."""

from dataclasses import dataclass

import httpx

from pdrd_api_gateway.application.ports.pdf_selection import (
    InvalidPdfSelectionError,
    PdfSelectionUnavailableError,
)
from pdrd_api_gateway.core.settings import DocumentServiceSettings


@dataclass(frozen=True, slots=True)
class HttpPdfSelectionValidator:
    """Передаёт PDF без рендеринга и сохраняет причину отказа Document Service."""

    settings: DocumentServiceSettings
    transport: httpx.AsyncBaseTransport | None = None

    async def validate(
        self, *, content: bytes, file_name: str, pages: str | None
    ) -> None:
        """Отказ в диапазоне становится 422, сбой сервиса — безопасным 503."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self.settings.request_timeout_seconds,
                    connect=self.settings.connect_timeout_seconds,
                ),
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.settings.base_url.rstrip('/')}/internal/v1/pdf/inspect",
                    files={"file": (file_name, content, "application/pdf")},
                    data={"pages": pages or ""},
                )
            if response.status_code in {400, 413, 422}:
                detail = response.json().get("detail")
                if isinstance(detail, str) and detail:
                    raise InvalidPdfSelectionError(detail)
            response.raise_for_status()
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("selected_pages"), list)
                or not payload["selected_pages"]
            ):
                raise ValueError("Некорректное подтверждение выбора страниц")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
            if isinstance(error, InvalidPdfSelectionError):
                raise
            raise PdfSelectionUnavailableError(
                "Не удалось проверить страницы PDF. Повторите попытку позже."
            ) from error
