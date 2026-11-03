# services/api-gateway/src/pdrd_api_gateway/infrastructure/equipment_export.py

"""Читает snapshots у EQ, а PDF строит у существующего Document Service."""

from dataclasses import dataclass

import httpx

from pdrd_api_gateway.core.settings import DocumentServiceSettings
from pdrd_api_gateway.infrastructure.equipment_search import EquipmentSearchClient


@dataclass(frozen=True, slots=True)
class HttpEquipmentExport:
    """Не открывает volume или внешние URL из пользовательского запроса."""

    equipment: EquipmentSearchClient
    document_settings: DocumentServiceSettings

    async def document(self, source_id: str) -> tuple[bytes, str]:
        """Получает ровно сохранённые байты по source ID."""
        return await self.equipment.document(source_id)

    async def report_pdf(self, job_id: str, revision: int, rows: list[dict]) -> bytes:
        """Передаёт закреплённый отчёт в ограниченный внутренний PDF renderer."""
        async with httpx.AsyncClient(
            timeout=90, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.post(
                self.document_settings.base_url.rstrip("/")
                + "/internal/v1/equipment-documents/report-pdf",
                json={"job_id": job_id, "revision": revision, "rows": rows},
            )
            response.raise_for_status()
            return response.content
