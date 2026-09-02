# services/api-gateway/src/pdrd_api_gateway/infrastructure/orchestration/n8n.py

"""HTTP adapter запуска PDRD workflow через n8n."""

from pathlib import Path
from typing import Any

import httpx

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.application.ports.orchestration import (
    AnalysisOrchestrationError,
)
from pdrd_api_gateway.core.settings import (
    OrchestrationSettings,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSourceMode,
)


class N8nAnalysisOrchestrator:
    """Запускает один из трёх опубликованных V2 workflow."""

    def __init__(
        self,
        *,
        settings: OrchestrationSettings,
    ) -> None:
        """Сохраняет настройки n8n adapter."""
        self._settings = settings

    async def execute(
        self,
        *,
        artifacts: AnalysisRequestArtifacts,
    ) -> dict[str, Any]:
        """Передаёт исходные файлы в нужный n8n webhook."""
        submission = artifacts.submission

        endpoint = self._resolve_endpoint(
            submission.source_mode,
        )

        files = self._build_files(
            artifacts,
        )

        data: dict[str, str] = {
            "document_id": str(
                submission.document_id,
            ),
            "use_explanatory_note": (
                "true" if submission.use_explanatory_note else "false"
            ),
        }

        if submission.pages is not None:
            data["pages"] = submission.pages

        if submission.use_explanatory_note:
            if submission.note_start_page is None or submission.note_end_page is None:
                raise AnalysisOrchestrationError(
                    "Для включённого контекста ПЗ отсутствует диапазон страниц.",
                )

            data["note_start_page"] = str(
                submission.note_start_page,
            )

            data["note_end_page"] = str(
                submission.note_end_page,
            )

        url = self._settings.base_url.rstrip("/") + endpoint

        timeout = httpx.Timeout(
            timeout=(self._settings.request_timeout_seconds),
            connect=(self._settings.connect_timeout_seconds),
        )

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
            ) as client:
                response = await client.post(
                    url,
                    files=files,
                    data=data,
                )

                response.raise_for_status()

        except httpx.HTTPStatusError as error:
            response_text = error.response.text[:1000]

            raise AnalysisOrchestrationError(
                "n8n workflow завершился HTTP ошибкой: "
                f"{error.response.status_code}. "
                f"Ответ: {response_text}"
            ) from error

        except httpx.HTTPError as error:
            raise AnalysisOrchestrationError(
                "Не удалось выполнить HTTP-запрос к n8n: "
                f"{type(error).__name__}: {error}"
            ) from error

        try:
            payload = response.json()

        except ValueError as error:
            raise AnalysisOrchestrationError(
                "n8n вернул невалидный JSON.",
            ) from error

        if not isinstance(
            payload,
            dict,
        ):
            raise AnalysisOrchestrationError(
                "n8n должен вернуть JSON object.",
            )

        if payload.get("status") != "completed":
            raise AnalysisOrchestrationError(
                "n8n workflow не подтвердил успешное завершение анализа.",
            )

        return payload

    def _resolve_endpoint(
        self,
        source_mode: AnalysisSourceMode,
    ) -> str:
        """Возвращает webhook для режима заявки."""
        if source_mode is AnalysisSourceMode.PDF_ONLY:
            return self._settings.pdf_webhook_path

        if source_mode is AnalysisSourceMode.CAD_ONLY:
            return self._settings.cad_webhook_path

        if source_mode is AnalysisSourceMode.PDF_CAD:
            return self._settings.pdf_cad_webhook_path

        raise AnalysisOrchestrationError(
            f"Неизвестный source_mode: {source_mode}.",
        )

    @staticmethod
    def _build_files(
        artifacts: AnalysisRequestArtifacts,
    ) -> dict[
        str,
        tuple[
            str,
            bytes,
            str,
        ],
    ]:
        """Формирует multipart files для n8n."""
        submission = artifacts.submission

        files: dict[
            str,
            tuple[
                str,
                bytes,
                str,
            ],
        ] = {}

        if artifacts.pdf_content is not None:
            pdf_file_name = submission.pdf_file_name or "document.pdf"

            files["pdf"] = (
                pdf_file_name,
                artifacts.pdf_content,
                "application/pdf",
            )

        if artifacts.cad_content is not None:
            cad_file_name = submission.cad_file_name or "drawing.dxf"

            files["cad"] = (
                cad_file_name,
                artifacts.cad_content,
                N8nAnalysisOrchestrator._cad_mime_type(
                    cad_file_name,
                ),
            )

        if submission.source_mode is AnalysisSourceMode.PDF_ONLY and "pdf" not in files:
            raise AnalysisOrchestrationError(
                "Для pdf_only отсутствует сохранённый PDF.",
            )

        if submission.source_mode is AnalysisSourceMode.CAD_ONLY and "cad" not in files:
            raise AnalysisOrchestrationError(
                "Для cad_only отсутствует сохранённый CAD.",
            )

        if submission.source_mode is AnalysisSourceMode.PDF_CAD and (
            "pdf" not in files or "cad" not in files
        ):
            raise AnalysisOrchestrationError(
                "Для pdf_cad требуются PDF и CAD.",
            )

        return files

    @staticmethod
    def _cad_mime_type(
        file_name: str,
    ) -> str:
        """Возвращает MIME type CAD upload."""
        extension = Path(
            file_name,
        ).suffix.lower()

        if extension == ".dxf":
            return "application/dxf"

        return "application/octet-stream"
