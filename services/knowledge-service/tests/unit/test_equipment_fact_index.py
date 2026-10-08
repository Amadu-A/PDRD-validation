# services/knowledge-service/tests/unit/test_equipment_fact_index.py

"""Проверяет холодный и повторный EQ-вызов через порт без PostgreSQL."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from pdrd_knowledge_service.application.equipment_fact_index import EquipmentFactIndex


@dataclass
class MemoryCache:
    """Хранит один набор и считает записи для проверки повторного использования."""

    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    writes: int = 0

    async def get(self, source_id: str) -> dict[str, Any] | None:
        """Возвращает сохранённый неизменяемый набор."""
        return self.values.get(source_id)

    async def save(self, result: dict[str, Any]) -> None:
        """Имитирует идемпотентную запись репозитория."""
        self.writes += 1
        self.values.setdefault(result["document_source_id"], result)


@pytest.mark.asyncio
async def test_cold_extraction_and_warm_cache_preserve_facts(monkeypatch) -> None:
    """Повторный вызов не извлекает факты снова и не меняет snapshot."""
    cache = MemoryCache()
    index = EquipmentFactIndex(cache)
    args = {
        "source_id": "EQ-snapshot",
        "sha256": "a" * 64,
        "manufacturer": "MEAN WELL",
        "model": "DRC-100B",
        "variant": "",
        "pages": [{"page": 1, "text": "DRC-100B Output voltage 24 V DC"}],
    }
    first = await index.extract_or_get(**args)
    assert first["facts"] and first["status"] == "ready"
    assert first["cache_hit"] is False and cache.writes == 1

    def forbidden(**kwargs):
        """Отказывает, если повторный вызов запускает извлечение."""
        raise AssertionError("Повторное извлечение вместо кэша")

    monkeypatch.setattr(
        "pdrd_knowledge_service.application.equipment_fact_index.extract_equipment_facts",
        forbidden,
    )
    second = await index.extract_or_get(**{**args, "pages": []})
    assert second["cache_hit"] is True and cache.writes == 1
    assert first["facts"] == second["facts"]
    with pytest.raises(ValueError, match="snapshot"):
        await index.extract_or_get(**{**args, "sha256": "b" * 64})


@pytest.mark.asyncio
async def test_concurrent_cache_insert_cannot_substitute_snapshot() -> None:
    """Конфликт конкурентной записи проверяется и после идемпотентного save."""

    class RacingCache(MemoryCache):
        """Имитирует другой snapshot, появившийся между get и save."""

        async def save(self, result: dict[str, Any]) -> None:
            """Сохраняет конкурирующий SHA с тем же source ID."""
            await super().save({**result, "document_sha256": "b" * 64})

    index = EquipmentFactIndex(RacingCache())
    with pytest.raises(ValueError, match="snapshot"):
        await index.extract_or_get(
            source_id="EQ-race",
            sha256="a" * 64,
            manufacturer="MEAN WELL",
            model="DRC-100B",
            variant="",
            pages=[],
        )
