# services/analysis-service/src/pdrd_analysis_service/infrastructure/normative_protocol.py

"""Ограниченный upload полного аудита напрямую в Gateway, минуя n8n."""

import asyncio
import hashlib
from dataclasses import dataclass

import httpx

from pdrd_analysis_service.application.ports.normative_protocol import (
    NormativeProtocolUnavailable,
)
from pdrd_analysis_service.application.vision_execution import AnalysisCancelledError
from pdrd_analysis_service.domain.normative_protocol import canonical_json


@dataclass(frozen=True, slots=True)
class HttpNormativeProtocolWriter:
    """Идемпотентная публикация content-addressed архива с проверкой подтверждения."""

    base_url: str
    timeout_seconds: float = 60.0
    transport: httpx.AsyncBaseTransport | None = None

    async def save(self, *, archive, check_cancellation):
        """Не повторяет GPU-работу; при обрыве безопасно повторяется только текущая часть."""
        raw = await asyncio.to_thread(canonical_json, archive)
        protocol_id = hashlib.sha256(raw).hexdigest()
        path = f"/internal/v1/analysis-artifacts/{archive['document_id']}/normative-protocols/{protocol_id}"
        part_bytes = 256 * 1024
        manifest = {
            "part_count": (len(raw) + part_bytes - 1) // part_bytes,
            "bytes": len(raw),
            "section_id": archive["section_id"],
            "document_ids": archive["document_ids"],
            "counts": archive["counts"],
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                for index in range(manifest["part_count"]):
                    await check_cancellation()
                    await self._request(
                        client,
                        "PUT",
                        path + f"/parts/{index}",
                        check_cancellation=check_cancellation,
                        content=raw[index * part_bytes : (index + 1) * part_bytes],
                    )
                await check_cancellation()
                response = await self._request(
                    client,
                    "POST",
                    path + "/publish",
                    json=manifest,
                    check_cancellation=check_cancellation,
                )
                result = response.json()
                if (
                    result.get("state") != "ready"
                    or result.get("protocol_id") != protocol_id
                    or result.get("document_id") != archive["document_id"]
                    or result.get("counts") != archive["counts"]
                ):
                    raise NormativeProtocolUnavailable(
                        "Gateway не подтвердил полный протокол."
                    )
                return result
        except (httpx.HTTPError, ValueError) as error:
            raise NormativeProtocolUnavailable(
                "Не удалось сохранить нормативный протокол."
            ) from error

    async def _request(self, client, method, path, *, check_cancellation, **kwargs):
        """Ограничивает сетевой retry; прикладные 4xx/5xx не скрываются повтором анализа."""
        for attempt in range(3):
            await check_cancellation()
            try:
                response = await client.request(method, path, **kwargs)
                if (
                    response.status_code == 409
                    and response.json().get("detail") == "analysis_cancelled"
                ):
                    raise AnalysisCancelledError(
                        "Задание отменено перед публикацией протокола."
                    )
                response.raise_for_status()
                return response
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.1 * 2**attempt)
