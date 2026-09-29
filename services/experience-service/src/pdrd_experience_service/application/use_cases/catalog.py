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
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError


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
        if entry.example.deleted:
            raise LookupError("Пример Experience удалён.")
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
        if entry.example.deleted:
            raise LookupError("Пример Experience удалён.")
        if not 0 <= index < len(entry.example.crops):
            raise LookupError("Область примера не найдена.")
        return await self.crops.read(entry.example.crops[index])

    async def history(self, example_id: UUID) -> tuple[dict, ...]:
        """Существование проверяется до выдачи неизменяемого журнала."""
        await self.get(example_id)
        return await self.repository.history(example_id)

    async def delete_many(
        self, *, references: tuple[tuple[UUID, int], ...], actor: str
    ) -> dict:
        """Удаляет выбранные строки целиком либо сообщает конфликт без частичной записи."""
        if (
            not references
            or len(references) > 1000
            or len({item[0] for item in references}) != len(references)
        ):
            raise ReviewError("Выберите от 1 до 1000 различных замечаний.")
        if not actor.strip():
            raise ReviewError("Отсутствует серверный автор операции.")
        deleted = await self.repository.delete_many(references=references, actor=actor)
        return {"deleted": deleted}

    async def read_selection(self, references: tuple[tuple[UUID, int], ...]) -> dict:
        """Gateway получает задания всего выбора одним чтением для будущей проверки прав."""
        entries = await self.repository.get_many(tuple(dict(references)))
        if len(entries) != len(dict(references)):
            raise LookupError("Часть выбранных замечаний не найдена.")
        return {
            "items": [
                {
                    "id": str(entry.example.id),
                    "job_id": str(entry.example.source.job_id),
                }
                for entry in entries
            ]
        }
