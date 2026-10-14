# services/equipment-search-service/src/pdrd_equipment_search_service/infrastructure/knowledge_facts.py

"""HTTP-адаптер EQ Facts в существующий Knowledge Service."""

from dataclasses import dataclass
from typing import Any

import httpx

from pdrd_equipment_search_service.domain.equipment import (
    DocumentSnapshot,
    EquipmentIdentity,
)


@dataclass(slots=True)
class KnowledgeEquipmentFactIndex:
    """Передаёт только текст производителя и идентификатор snapshot."""

    client: httpx.AsyncClient
    base_url: str
    timeout_seconds: float = 90.0

    async def extract_or_get(
        self,
        identity: EquipmentIdentity,
        snapshot: DocumentSnapshot,
        pages: tuple[tuple[int, str], ...],
        *,
        vision_facts: tuple[dict[str, Any], ...] = (),
    ) -> tuple[str, tuple[dict[str, Any], ...]]:
        """Полагается на кэш Knowledge по SHA и версиям извлекателя."""
        response = await self.client.post(
            f"{self.base_url.rstrip('/')}/internal/v1/equipment-facts/extract",
            json={
                "source_id": snapshot.source_id,
                "sha256": snapshot.sha256,
                "manufacturer": snapshot.manufacturer,
                "model": snapshot.model,
                "variant": snapshot.variant,
                "properties": list(identity.properties[:8]),
                "vision_facts": list(vision_facts),
                "pages": [{"page": page, "text": content} for page, content in pages],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        facts = payload.get("facts", [])
        if not isinstance(facts, list):
            raise ValueError("Knowledge Service вернул некорректные EQ Facts.")
        status = (
            "partial_index"
            if payload.get("retrieval_status") == "unavailable"
            else str(payload.get("status") or "needs_review")
        )
        return status, tuple(facts)
