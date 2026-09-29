# services/experience-service/src/pdrd_experience_service/application/use_cases/catalog.py

"""Сценарии чтения, инженерной редакции и безопасной деактивации Experience."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pdrd_experience_service.application.ports.catalog import (
    CatalogRepository,
    CropStore,
)
from pdrd_experience_service.core.observability import log_execution_time
from pdrd_experience_service.domain.catalog import CatalogEntry, CatalogFilter
from pdrd_experience_service.domain.review import ReviewConflictError


@dataclass(frozen=True, slots=True)
class ManageCatalog:
    """CRUD не переписывает операционный Review и исходный обучающий материал."""

    repository: CatalogRepository
    crops: CropStore

    async def list(self, criteria: CatalogFilter):
        """Пагинацию и фильтрацию выполняет репозиторий на сервере."""
        return await self.repository.list(criteria)

    async def get(self, example_id: UUID) -> CatalogEntry:
        """Отсутствующий пример имеет самостоятельную ожидаемую ошибку."""
        entry = await self.repository.get(example_id)
        if entry is None:
            raise LookupError("Пример Experience не найден.")
        return entry

    @log_execution_time(operation="experience_curate")
    async def update(
        self, *, example_id: UUID, expected_revision: int, fields: dict, actor: str
    ) -> CatalogEntry:
        """CAS защищает от перезаписи правок второй вкладки."""
        entry = await self.get(example_id)
        if entry.example.revision != expected_revision:
            raise ReviewConflictError(
                "Пример уже изменён; загрузите актуальную редакцию."
            )
        revised = entry.example.curate(fields=fields, actor=actor, at=datetime.now(UTC))
        return await self.repository.update(
            example=revised, expected_revision=expected_revision
        )

    async def image(self, *, example_id: UUID, index: int) -> bytes:
        """Не принимает путь или хеш от браузера: только номер области своего примера."""
        entry = await self.get(example_id)
        if not 0 <= index < len(entry.example.crops):
            raise LookupError("Область примера не найдена.")
        return await self.crops.read(entry.example.crops[index])

    async def history(self, example_id: UUID) -> tuple[dict, ...]:
        """Существование проверяется до выдачи неизменяемого журнала."""
        await self.get(example_id)
        return await self.repository.history(example_id)
