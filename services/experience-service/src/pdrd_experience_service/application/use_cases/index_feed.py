# services/experience-service/src/pdrd_experience_service/application/use_cases/index_feed.py

"""Чтение E-проекций и проверенных PNG для отдельного индексатора Knowledge.

Повторная проверка версии исключает правку, отзыв области и деактивацию каталога.
Сценарий только читает собственные порты, не обращается к Qdrant и embedding.
"""

from dataclasses import dataclass
from uuid import UUID

from pdrd_experience_service.application.ports.catalog import (
    CatalogRepository,
    CropStore,
)
from pdrd_experience_service.domain.index_projection import index_projection
from pdrd_experience_service.domain.review import ReviewConflictError


@dataclass(frozen=True, slots=True)
class ReadIndexFeed:
    """Отдаёт только актуальные однозначные примеры, независимо от пользовательских фильтров."""

    catalog: CatalogRepository
    crops: CropStore

    async def page(self, *, after: UUID | None = None, limit: int = 50) -> dict:
        """Продолжает обход даже после страницы, целиком состоящей из исключённых примеров."""
        if not 1 <= limit <= 100:
            raise ValueError("Размер страницы E должен быть от 1 до 100.")
        entries = await self.catalog.scan(after=after, limit=limit)
        items = [
            projected
            for entry in entries
            if (projected := index_projection(entry)) is not None
        ]
        return {
            "items": items,
            "next_after": str(entries[-1].example.id)
            if len(entries) == limit
            else None,
        }

    async def verify(self, references: tuple[dict, ...]) -> tuple[dict, ...]:
        """Возвращает серверное содержимое только для совпавшей актуальной версии."""
        entries = {
            entry.example.id: entry
            for entry in await self.catalog.get_many(
                tuple(
                    dict.fromkeys(
                        UUID(reference["example_id"]) for reference in references
                    )
                )
            )
        }
        results = []
        for reference in references:
            entry = entries.get(UUID(reference["example_id"]))
            projected = None if entry is None else index_projection(entry)
            if projected is not None and all(
                projected[key] == reference[key]
                for key in ("example_revision", "fingerprint")
            ):
                results.append(projected)
        return tuple(results)

    async def crop(self, *, example_id: UUID, index: int, fingerprint: str) -> bytes:
        """Сверяет редакцию до и после чтения PNG, проверяемого CropStore по SHA-256."""
        entry = await self.catalog.get(example_id)
        projected = None if entry is None else index_projection(entry)
        if projected is None or projected["fingerprint"] != fingerprint:
            raise ReviewConflictError("Пример E изменён или больше не пригоден.")
        if not 0 <= index < len(entry.example.crops):
            raise LookupError("Область примера не найдена.")
        content = await self.crops.read(entry.example.crops[index])
        current = await self.catalog.get(example_id)
        if current is None or index_projection(current) != projected:
            raise ReviewConflictError("Пример E изменён во время чтения области.")
        return content
