# services/experience-service/src/pdrd_experience_service/infrastructure/analysis/http_source.py

"""Читает исходный PDF и результат через закрытый API Gateway.

Артефакты проверяются VerifiedCompletedAnalysisReader. URL и служебный ключ
задаёт сервер; пользователь не может указать источник или подменить оригинал.
"""

import base64
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from pdrd_experience_service.application.ports.analysis_source import (
    AnalysisSourceUnavailableError,
    CompletedAnalysisArtifacts,
)
from pdrd_experience_service.domain.review import ReviewError


@dataclass(frozen=True, slots=True)
class GatewayAnalysisSource:
    """Адаптер серверного источника с ограниченным временем ожидания."""

    base_url: str
    internal_key: str = field(repr=False)
    timeout_seconds: float = 300
    transport: httpx.AsyncBaseTransport | None = None

    async def load_completed(self, job_id: UUID) -> CompletedAnalysisArtifacts:
        """Не принимает browser payload и не следует перенаправлениям HTTP."""
        try:
            return await self._load(job_id)
        except ReviewError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise AnalysisSourceUnavailableError(
                "Серверный источник анализа временно недоступен."
            ) from error

    async def _load(self, job_id: UUID) -> CompletedAnalysisArtifacts:
        """Разбирает закрытый ответ; ошибки деталей не раскрываются браузеру."""
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            response = await client.get(
                f"{self.base_url.rstrip('/')}/internal/v1/review-source/{job_id}",
                headers={"Authorization": f"Bearer {self.internal_key}"},
            )
        if response.status_code == 404:
            raise LookupError("Завершённый анализ не найден.")
        if response.status_code in {409, 422}:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("Некорректный ответ источника.")
            detail = payload.get("detail")
            raise ReviewError(
                detail if isinstance(detail, str) else "Исходник недоступен для Review."
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Некорректный ответ серверного источника анализа.")
        section_id, section_title = (
            payload.get("section_id", ""),
            payload.get("section_title", ""),
        )
        if (
            not isinstance(section_id, str)
            or not isinstance(section_title, str)
            or len(section_title) > 300
        ):
            raise TypeError(
                "Некорректный раздел нормативной базы в серверном источнике."
            )
        if section_id:
            section_id = str(UUID(section_id))
        return CompletedAnalysisArtifacts(
            job_id=UUID(payload["job_id"]),
            document_id=UUID(payload["document_id"]),
            status=payload["status"],
            source_filename=payload["source_filename"],
            source_sha256=payload["source_sha256"],
            pdf_content=base64.b64decode(payload["pdf_base64"], validate=True),
            result=payload["result"],
            visualization=payload["visualization"],
            section_id=section_id,
            section_title=section_title.strip(),
        )
