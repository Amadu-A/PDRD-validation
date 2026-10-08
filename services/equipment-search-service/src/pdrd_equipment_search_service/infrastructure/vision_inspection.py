# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/vision_inspection.py

"""Адресное чтение скана через Document Service и общий Analysis Service."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

import httpx

from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DownloadedDocument,
    EquipmentIdentity,
)


@dataclass(slots=True)
class TargetedVisionInspector:
    """Запрашивает не более двух выбранных страниц и расходует бюджет задания."""

    client: httpx.AsyncClient
    document_base_url: str
    analysis_base_url: str
    max_pages: int = 2
    timeout_seconds: float = 60.0

    async def inspect(
        self,
        identity: EquipmentIdentity,
        document: DownloadedDocument,
        inspection: DocumentInspection,
        *,
        budget: EquipmentVisionBudget,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> DocumentInspection:
        """Сохраняет визуальные факты только при точной идентификации модели."""
        candidates = tuple(page for page, _ in inspection.pages)[: self.max_pages] or (
            1,
        )
        for page in candidates:
            if should_stop is not None and await should_stop():
                raise asyncio.CancelledError
            if budget.remaining <= 0:
                return replace(inspection, reason="Исчерпан бюджет адресного EQ VLM.")
            try:
                raster = await self.client.post(
                    self.document_base_url.rstrip("/")
                    + "/internal/v1/equipment-documents/render-page",
                    files={
                        "file": (
                            "documentation.pdf",
                            document.content,
                            "application/pdf",
                        )
                    },
                    data={"page_number": str(page)},
                    timeout=30,
                )
                raster.raise_for_status()
                if should_stop is not None and await should_stop():
                    raise asyncio.CancelledError
                if not budget.claim():
                    return replace(
                        inspection, reason="Исчерпан бюджет адресного EQ VLM."
                    )
                response = await self.client.post(
                    self.analysis_base_url.rstrip("/")
                    + "/internal/v1/stages/equipment-document-vision",
                    json={
                        "manufacturer": identity.manufacturer,
                        "model": identity.model,
                        "variant": identity.variant,
                        "properties": list(identity.properties[:8]),
                        "page": page,
                        "image_base64": raster.json()["image_base64"],
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, KeyError, ValueError):
                return replace(
                    inspection,
                    reason="Адресное визуальное извлечение временно недоступно.",
                )
            if not isinstance(payload, dict):
                continue
            if isinstance(payload.get("metrics"), dict):
                budget.metrics.append(payload["metrics"])
            facts = payload.get("facts")
            if (
                payload.get("identified") is True
                and isinstance(facts, list)
                and any(isinstance(fact, dict) for fact in facts)
            ):
                return replace(
                    inspection,
                    applicable=True,
                    requires_vision=False,
                    reason="",
                    vision_facts=tuple(
                        {**fact, "page": page}
                        for fact in facts[:8]
                        if isinstance(fact, dict)
                    ),
                )
        return inspection
