# services/knowledge-service/src/pdrd_knowledge_service/application/ports/experience_versions.py

"""Порт аренды ручного задания; Knowledge не читает SQL владельца реестра."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pdrd_knowledge_service.domain.experience_index import TrustedExample


@dataclass(frozen=True, slots=True)
class VersionIndexJob:
    """Владелец реестра выдаёт фиксированный состав и серверное имя коллекции."""

    id: UUID
    collection: str
    identity: str
    dimension: int
    section_id: str
    members: tuple[TrustedExample, ...]
    manifest_sha256: str = ""


class ExperienceVersionQueue(Protocol):
    """Статус задания не предоставляет прав изменять каталог или решения инженера."""

    async def claim(
        self, *, worker: str, model: str, identity: str, dimension: int
    ) -> VersionIndexJob | None:
        """Получает одну совместимую задачу под арендой."""
        ...

    async def finish(
        self, *, job_id: UUID, worker: str, status: str, error: str = ""
    ) -> None:
        """Продлевает аренду или завершает задание."""
        ...
