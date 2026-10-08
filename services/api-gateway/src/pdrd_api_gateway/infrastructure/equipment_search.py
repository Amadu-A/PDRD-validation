# services/api-gateway/src/pdrd_api_gateway/infrastructure/equipment_search.py

"""Внутренний клиент EQ-заданий без передачи ключа в браузер."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from pdrd_api_gateway.core.settings import EquipmentSearchSettings


@dataclass(frozen=True, slots=True)
class EquipmentSearchClient:
    """Проксирует только ограниченные внутренние контракты."""

    settings: EquipmentSearchSettings

    def _headers(self) -> dict[str, str]:
        """Добавляет закрытый ключ к server-to-server запросу."""
        if (
            not self.settings.enabled
            or not self.settings.internal_key.get_secret_value()
        ):
            raise RuntimeError("Equipment Search не настроен.")
        return {
            "X-Internal-Key": self.settings.internal_key.get_secret_value(),
        }

    def _base(self) -> str:
        """Возвращает внутренний URL без клиентского ввода."""
        return self.settings.base_url.rstrip("/") + "/internal/v1/equipment-search"

    async def start(
        self,
        document_id: UUID,
        identities: list[dict],
        allow_unverified: bool,
    ) -> dict[str, Any]:
        """Идемпотентно запускает поиск моделей из Understanding."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{self._base()}/jobs/{document_id}",
                headers=self._headers(),
                json={
                    "identities": identities,
                    "allow_unverified": allow_unverified,
                },
            )
            response.raise_for_status()
            return response.json()

    async def state(self, document_id: UUID) -> dict[str, Any]:
        """Читает состояние и факты без потокового соединения."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.get(
                f"{self._base()}/jobs/{document_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def cancel(self, document_id: UUID) -> None:
        """Передаёт отмену EQ-ветви по document ID анализа."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{self._base()}/jobs/{document_id}/cancel",
                headers=self._headers(),
            )
            if response.status_code != 404:
                response.raise_for_status()

    async def wait(self, document_id: UUID) -> dict[str, Any]:
        """Ограниченно ждёт EQ, сохраняя основной анализ при ошибке."""
        state: dict[str, Any] = {"results": []}
        try:
            async with asyncio.timeout(self.settings.wait_seconds):
                while True:
                    try:
                        state = await self.state(document_id)
                    except (httpx.HTTPError, RuntimeError, ValueError):
                        return {
                            **state,
                            "status": "incomplete",
                            "warning": "Equipment Search временно недоступен.",
                        }
                    if state.get("status") in {"completed", "incomplete", "cancelled"}:
                        return state
                    await asyncio.sleep(1)
        except TimeoutError:
            with suppress(httpx.HTTPError, RuntimeError, ValueError, TimeoutError):
                async with asyncio.timeout(min(5.0, self.settings.timeout_seconds)):
                    await self.cancel(document_id)
                    state = await self.state(document_id)
            return {
                **state,
                "status": "incomplete",
                "warning": "Превышен бюджет ожидания Equipment Search.",
            }

    async def sources(
        self,
        *,
        status: str | None = None,
        query: str = "",
    ) -> list[dict]:
        """Возвращает каталог для пользователя с правом управления."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.get(
                f"{self._base()}/sources",
                headers=self._headers(),
                params=(
                    {"query": query, "source_status": status}
                    if status
                    else {"query": query}
                ),
            )
            response.raise_for_status()
            return response.json()

    async def manufacturers(self) -> list[dict]:
        """Читает производителей и aliases."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.get(
                f"{self._base()}/manufacturers",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def update_source(self, payload: dict, actor: UUID) -> dict:
        """Передаёт проверенного сервером пользователя как инициатора."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.put(
                f"{self._base()}/sources",
                headers={**self._headers(), "X-Actor": str(actor)},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def document(self, source_id: str) -> tuple[bytes, str]:
        """Читает неизменяемый PDF/HTML snapshot по внутреннему ключу."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.get(
                f"{self._base()}/documents/{source_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.content, response.headers.get(
                "content-type",
                "application/octet-stream",
            ).split(";")[0]

    async def source_audit(self, source_id: UUID) -> list[dict]:
        """Читает историю решений по точному домену."""
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.get(
                f"{self._base()}/sources/{source_id}/audit",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()
