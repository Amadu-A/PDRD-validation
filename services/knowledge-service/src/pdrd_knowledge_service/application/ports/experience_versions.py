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


@dataclass(frozen=True, slots=True)
class AppliedExperienceVersion:
    """Применённый состав и отпечаток проверенного отчёта для одного раздела."""

    job: VersionIndexJob
    quality_sha256: str


class AppliedExperienceVersions(Protocol):
    """Читает рабочее назначение у владельца, не меняет его и не допускает версии."""

    async def applied(
        self,
        *,
        section_id: str,
        model: str,
        identity: str,
        dimension: int,
        top_k: int,
        min_score: float,
    ) -> AppliedExperienceVersion | None:
        """Возвращает только совместимую проверенную версию запрошенного раздела."""
        ...
