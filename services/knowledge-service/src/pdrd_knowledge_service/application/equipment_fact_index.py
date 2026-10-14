# services/knowledge-service/src/pdrd_knowledge_service/application/equipment_fact_index.py

"""Извлечение и повторное использование EQ Facts через порт хранения."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pdrd_knowledge_service.application.equipment_facts import (
    EXTRACTOR_VERSION,
    SCHEMA_VERSION,
    extract_equipment_facts,
)
from pdrd_knowledge_service.application.equipment_semantic_index import (
    EquipmentSemanticIndex,
)
from pdrd_knowledge_service.application.equipment_vision_facts import (
    normalize_equipment_vision_facts,
)
from pdrd_knowledge_service.core.observability import log_execution_time


class EquipmentFactCache(Protocol):
    """Порт версионированного хранения snapshot и его EQ Facts."""

    async def get(self, source_id: str) -> dict[str, Any] | None:
        """Читает факты текущих версий схемы и извлекателя."""

    async def save(self, result: dict[str, Any]) -> None:
        """Идемпотентно сохраняет набор без перезаписи исторической версии."""


@dataclass(slots=True)
class EquipmentFactIndex:
    """Управляет extraction, retrieval и кэшем без знания SQL драйвера."""

    cache: EquipmentFactCache
    semantic_index: EquipmentSemanticIndex | None = None

    @log_execution_time(operation="equipment.extract_or_get")
    async def extract_or_get(
        self,
        *,
        source_id: str,
        sha256: str,
        manufacturer: str,
        model: str,
        variant: str,
        properties: tuple[str, ...] = (),
        pages: list[dict[str, Any]],
        vision_facts: tuple[dict[str, Any], ...] = (),
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        """Извлекает один раз для текущей пары версий схемы и алгоритма."""
        cached = await self.cache.get(source_id)
        if cached is not None:
            if (
                cached["document_sha256"] != sha256
                or cached["manufacturer"] != manufacturer
                or cached["model"] != model
                or cached["variant"] != variant
            ):
                raise ValueError("EQ source ID уже связан с другим snapshot.")
            return {**cached, "cache_hit": True}
        ranked_pages: tuple[int, ...] = ()
        retrieval_status = "disabled"
        if self.semantic_index is not None:
            try:
                ranked_pages = await self.semantic_index.rank_pages(
                    source_id=source_id,
                    document_sha256=sha256,
                    extractor_version=EXTRACTOR_VERSION,
                    model=model,
                    properties=properties,
                    pages=pages,
                    should_stop=should_stop,
                )
                retrieval_status = "ready" if ranked_pages else "empty"
            except Exception:
                retrieval_status = "unavailable"
        priority = {page: index for index, page in enumerate(ranked_pages)}
        ordered_pages = sorted(
            pages,
            key=lambda page: priority.get(int(page.get("page", 0)), len(priority)),
        )
        if should_stop is not None and await should_stop():
            raise asyncio.CancelledError
        facts = extract_equipment_facts(
            source_id=source_id,
            model=model,
            variant=variant,
            pages=ordered_pages,
        )
        visual_only = not facts and bool(vision_facts)
        if visual_only:
            facts = normalize_equipment_vision_facts(
                source_id=source_id,
                model=model,
                variant=variant,
                facts=vision_facts,
            )
        result = {
            "document_source_id": source_id,
            "document_sha256": sha256,
            "manufacturer": manufacturer,
            "model": model,
            "variant": variant,
            "schema_version": SCHEMA_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "status": "ready" if facts and not visual_only else "needs_review",
            "facts": facts,
        }
        await self.cache.save(result)
        persisted = await self.cache.get(source_id)
        if persisted is None:
            raise RuntimeError("Не удалось сохранить EQ Facts.")
        if any(
            persisted[key] != result[key]
            for key in ("document_sha256", "manufacturer", "model", "variant")
        ):
            raise ValueError("EQ source ID уже связан с другим snapshot.")
        return {
            **persisted,
            "cache_hit": False,
            "retrieval_status": retrieval_status,
            "retrieved_pages": ranked_pages,
        }

    async def get(self, source_id: str) -> dict[str, Any] | None:
        """Читает текущие EQ Facts через тот же порт хранения."""
        return await self.cache.get(source_id)
