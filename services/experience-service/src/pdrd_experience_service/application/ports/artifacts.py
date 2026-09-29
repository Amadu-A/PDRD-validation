# services/experience-service/src/pdrd_experience_service/application/ports/artifacts.py

"""Порт реестра версий: запись состава, CAS, очередь и применённая конфигурация."""

from typing import Protocol
from uuid import UUID

from pdrd_experience_service.domain.artifacts import ArtifactVersion


class ArtifactRepository(Protocol):
    """Experience владеет метаданными; Knowledge создаёт только собственные Qdrant коллекции."""

    async def list(self) -> tuple[ArtifactVersion, ...]:
        """Возвращает доступные версии обоих видов."""
        ...

    async def get(self, version_id: UUID) -> ArtifactVersion | None:
        """Читает зафиксированный состав одной версии."""
        ...

    async def create(self, version: ArtifactVersion) -> None:
        """Сверяет редакции каталога и пишет весь состав одной транзакцией."""
        ...

    async def rename(
        self, *, version_id: UUID, revision: int, name: str, actor: str
    ) -> ArtifactVersion:
        """Переименование не меняет manifest."""
        ...

    async def delete(self, *, version_id: UUID, revision: int, actor: str) -> None:
        """Активную/строящуюся версию удалять нельзя; аудит остаётся."""
        ...

    async def claim(
        self, *, worker: str, model: str, identity: str, dimension: int
    ) -> ArtifactVersion | None:
        """Атомарная аренда queued/прерванной задачи одного совместимого worker."""
        ...

    async def finish(
        self, *, version_id: UUID, worker: str, result: dict
    ) -> ArtifactVersion:
        """Результат принимается только от владельца действующей аренды."""
        ...

    async def applied(self) -> tuple[dict, ...]:
        """Текущая рабочая конфигурация отделена от просмотра версий."""
        ...

    async def apply(self, *, version_id: UUID, revision: int, actor: str) -> dict:
        """Допускает только ready артефакт с одобренным качеством."""
        ...
