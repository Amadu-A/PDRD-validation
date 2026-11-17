# services/api-gateway/src/pdrd_api_gateway/infrastructure/analysis_pdf_export.py

"""Передача PDF и полного отчёта в Document Service без обрезания доказательств."""

import json
from uuid import uuid4

import httpx

from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisPdfAnnotation,
    AnalysisPdfExportError,
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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Сохраняет bounded HTTP settings."""
        self._base_url = settings.base_url.rstrip(
            "/",
        )

        self._request_timeout_seconds = settings.request_timeout_seconds

        self._connect_timeout_seconds = settings.connect_timeout_seconds
        self._transport = transport

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
        request_id = uuid4().hex

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    (f"{self._base_url}/internal/v1/pdf/annotate"),
                    headers={"X-Request-ID": request_id},
                    files={
                        "file": (
                            file_name,
                            pdf_content,
                            "application/pdf",
                        ),
                        # Текстовое поле multipart ограничено Starlette одним МБ.
                        # Файл JSON сохраняет полный отчёт и использует spool на диске.
                        "payload": (
                            "annotations.json",
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(
                                    ",",
                                    ":",
                                ),
                            ).encode("utf-8"),
                            "application/json",
                        ),
                    },
                )

                response.raise_for_status()

        except httpx.TimeoutException as error:
            raise AnalysisPdfExportError(
                code="pdf_timeout", status_code=504, request_id=request_id
            ) from error
        except httpx.HTTPStatusError as error:
            upstream_status = error.response.status_code
            code, status_code = (
                ("pdf_invalid_input", 422)
                if upstream_status in {400, 413, 422}
                else ("pdf_service_unavailable", 503)
                if upstream_status == 503
                else ("pdf_generation_failed", 502)
            )
            raise AnalysisPdfExportError(
                code=code, status_code=status_code, request_id=request_id
            ) from error
        except httpx.HTTPError as error:
            raise AnalysisPdfExportError(
                code="pdf_service_unavailable", status_code=503, request_id=request_id
            ) from error

        content = response.content

        if not content.startswith(
            b"%PDF-",
        ):
            raise AnalysisPdfExportError(
                code="pdf_invalid_response", status_code=502, request_id=request_id
            )

        return content
