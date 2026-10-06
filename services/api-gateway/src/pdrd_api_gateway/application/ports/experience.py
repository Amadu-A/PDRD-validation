# services/api-gateway/src/pdrd_api_gateway/application/ports/experience.py

"""Контекст и порты каталога Experience с точкой будущей проверки прав."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

ExperienceOperation = Literal[
    "list",
    "authors",
    "read",
    "curate",
    "deactivate",
    "crop",
    "history",
    "export",
    "capture",
    "delete_selection",
    "selection_read",
    "version_list",
    "version_create",
    "version_read",
    "version_rename",
    "version_delete",
    "version_apply",
    "version_export",
    "version_quality",
    "version_quality_revoke",
]


@dataclass(frozen=True, slots=True)
class ExperienceContext:
    """Actor задаёт сервер; задание записи определяется её неизменяемым источником."""

    actor: str
    operation: ExperienceOperation
    job_id: UUID | None = None
    example_id: UUID | None = None


class ExperienceContextProvider(Protocol):
    """Будущая идентичность подключается без регистрации и токенов сейчас."""

    def resolve(
        self,
        *,
        operation: ExperienceOperation,
        job_id: UUID | None,
        example_id: UUID | None,
    ) -> ExperienceContext:
        """Получает субъекта из доверенной серверной конфигурации."""
        ...


class ExperienceAccessPolicy(Protocol):
    """Единая точка прав на каталог, операцию и задание перед выдачей данных."""

    async def require(self, context: ExperienceContext) -> None:
        """В будущем ограничивает чтение/редактирование по субъекту и заданию."""
        ...


class ExperienceService(Protocol):
    """Gateway не хранит примеры и не вырезает PDF самостоятельно."""

    async def execute(
        self,
        *,
        context: ExperienceContext,
        query: dict | None,
        command: dict | None,
        index: int | None,
    ) -> dict | bytes:
        """Выполняет фиксированную операцию каталога, без пользовательских URL."""
        ...


class ExperienceAuthorProfiles(Protocol):
    """User Service — единственный источник имени, логина и актуальных ролей автора."""

    async def read(
        self, *, actor_user_id: UUID, user_ids: tuple[UUID, ...]
    ) -> dict[UUID, dict]:
        """Пакетно читает ограниченную публичную проекцию без credential и паролей."""
        ...
