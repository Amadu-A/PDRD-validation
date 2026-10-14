# services/api-gateway/src/pdrd_api_gateway/infrastructure/equipment_comparison.py

"""Вызывает существующий детерминированный EQ этап Analysis Service."""

from dataclasses import dataclass

import httpx

from pdrd_api_gateway.core.settings import AnalysisServiceSettings


@dataclass(frozen=True, slots=True)
class HttpEquipmentComparison:
    """Не запускает второй VLM проход и не меняет другие источники анализа."""

    settings: AnalysisServiceSettings

    async def compare(self, pages: list[dict], search: dict) -> dict:
        """Передаёт только серверные факты, с ограничением времени и без proxy env."""
        async with httpx.AsyncClient(
            timeout=min(60, self.settings.request_timeout_seconds),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                self.settings.base_url.rstrip("/")
                + "/internal/v1/stages/compare-equipment",
                json={"pages": pages, "search": search},
            )
            response.raise_for_status()
            return response.json()
