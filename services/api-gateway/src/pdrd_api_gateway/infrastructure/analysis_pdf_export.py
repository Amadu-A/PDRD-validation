# services/api-gateway/src/pdrd_api_gateway/infrastructure/analysis_pdf_export.py

"""HTTP adapter Document Service annotated PDF export."""

import json

import httpx

from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisPdfAnnotation,
    AnalysisPdfReport,
)
from pdrd_api_gateway.core.settings import (
    DocumentServiceSettings,
)


class DocumentServiceAnalysisAnnotatedPdfRenderer:
    """Передаёт PDF/annotations/report в Document Service."""

    def __init__(
        self,
        *,
        settings: DocumentServiceSettings,
    ) -> None:
        """Сохраняет bounded HTTP settings."""
        self._base_url = settings.base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = settings.request_timeout_seconds

        self._connect_timeout_seconds = settings.connect_timeout_seconds

    async def render(
        self,
        *,
        pdf_content: bytes,
        file_name: str,
        annotations: tuple[
            AnalysisPdfAnnotation,
            ...,
        ],
        report: AnalysisPdfReport,
    ) -> bytes:
        """Строит PDF одним internal HTTP request."""
        timeout = httpx.Timeout(
            self._request_timeout_seconds,
            connect=(self._connect_timeout_seconds),
        )

        payload = {
            "annotations": [annotation.as_dict() for annotation in annotations],
            "report": report.as_dict(),
        }

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/pdf/annotate"),
                    files={
                        "file": (
                            file_name,
                            pdf_content,
                            "application/pdf",
                        ),
                    },
                    data={
                        "payload": json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(
                                ",",
                                ":",
                            ),
                        ),
                    },
                )

                response.raise_for_status()

        except httpx.HTTPError as error:
            raise RuntimeError(
                "Document Service не сформировал annotated PDF.",
            ) from error

        content = response.content

        if not content.startswith(
            b"%PDF-",
        ):
            raise RuntimeError(
                "Document Service вернул не-PDF annotation artifact.",
            )

        return content
