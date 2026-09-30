# services/experience-service/src/pdrd_experience_service/application/ports/catalog.py

"""Порты каталога, crop и транзакционного сохранения проверенной редакции."""

from typing import Protocol
from uuid import UUID

from pdrd_experience_service.domain.catalog import (
    CatalogEntry,
    CatalogFilter,
    Crop,
    Example,
)
from pdrd_experience_service.domain.experience_selection import ExperienceCandidate
from pdrd_experience_service.domain.review import Rectangle, ReviewSession


class CropRenderer(Protocol):
    """Document Service вырезает точные области; Experience не импортирует PDF runtime."""

    async def render(
        self, *, pdf: bytes, page_number: int, regions: tuple[Rectangle, ...]
    ) -> tuple[bytes, ...]:
        """Возвращает PNG по одному на каждую исходную или принятую область."""
        ...


class CropStore(Protocol):
    """Своё устойчивое хранилище PNG с проверкой хеша и безопасными адресами."""

    async def put(self, content: bytes) -> Crop:
        """Сохраняет crop идемпотентно, без исходного PDF."""
        ...

    async def read(self, crop: Crop) -> bytes:
        """Проверяет целостность перед выдачей или экспортом."""
        ...


class CatalogRepository(Protocol):
    """PostgreSQL хранит источник, редакции и аудит; PNG принадлежат CropStore."""

    async def capture(
        self,
        *,
        review: ReviewSession,
        area_versions: tuple,
        examples: tuple[Example, ...],
        actor: str,
        expected_revisions: tuple[tuple[UUID, int], ...] = (),
    ) -> dict:
        """Атомарно проверяет Review/подтверждения и вставляет новые примеры."""
        ...

    async def find_source(self, source: ExperienceCandidate) -> CatalogEntry | None:
        """Находит семантический дубль или прежний снимок того же утверждения."""
        ...

    async def list(
        self, criteria: CatalogFilter
    ) -> tuple[tuple[CatalogEntry, ...], int]:
        """Фильтрует и считает записи на сервере."""
        ...

    async def get(self, example_id: UUID) -> CatalogEntry | None:
        """Вычисляет актуальность Review и координат при каждом чтении."""
        ...

    async def get_many(self, example_ids: tuple[UUID, ...]) -> tuple[CatalogEntry, ...]:
        """Читает выбранные редакции с актуальностью одним запросом, без N+1."""
        ...

    async def update(self, *, example: Example, expected_revision: int) -> CatalogEntry:
        """Пишет редакцию и неизменяемый аудит с CAS."""
        ...

    async def history(self, example_id: UUID) -> tuple[dict, ...]:
        """Возвращает редакции и серверных авторов для просмотра аудита."""
        ...

    async def delete_many(
        self, *, references: tuple[tuple[UUID, int], ...], actor: str
    ) -> int:
        """CAS и удаление всего набора с сохранением аудита — одна транзакция."""
        ...

    async def scan(self, *, after: UUID | None, limit: int) -> tuple[CatalogEntry, ...]:
        """Читает стабильную страницу по UUID, включая неактивные записи для отбора."""
        ...
