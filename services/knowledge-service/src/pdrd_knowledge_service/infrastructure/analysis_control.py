# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/analysis_control.py

"""Проверка lifecycle через владельца задания без чтения чужой базы данных."""

from uuid import UUID

import httpx

from pdrd_knowledge_service.application.ports.embedding import EmbeddingTemporaryError


class HttpAnalysisControl:
    """Читает durable отмену и остаток времени через внутренний Gateway API."""

    def __init__(self, base_url: str) -> None:
        """Сохраняет внутренний адрес; пользователь не может подменить его заголовком."""
        self._base_url = base_url.rstrip("/")

    async def get(self, document_id: UUID) -> dict:
        """При недоступном контроле запрещает новый inference вместо обхода deadline."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(
                    f"{self._base_url}/internal/v1/analysis-progress/{document_id}"
                )
                response.raise_for_status()
                result = response.json()
                UUID(result["job_id"])
                float(result["remaining_seconds"])
                return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise EmbeddingTemporaryError(
                "Не удалось проверить lifecycle анализа.",
                reason_code="analysis_control_unavailable",
            ) from error
