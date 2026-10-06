# services/user-service/src/pdrd_user_service/application/use_cases/distribute_section.py

"""Однократно выдаёт новый раздел действующим проектировщикам и руководителям."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pdrd_user_service.application.ports.repository import (
    UnitOfWorkFactory,
    UserRepository,
)
from pdrd_user_service.application.ports.section_catalog import SectionCatalog
from pdrd_user_service.application.use_cases.users import UserNotFound
from pdrd_user_service.core.observability import log_execution_time
from pdrd_user_service.domain.access import Permission, Role, effective_permissions
from pdrd_user_service.domain.identity import UserAccount
from pdrd_user_service.domain.role_assignments import (
    access_subject_for,
    effective_roles,
)


class CatalogWriteRequired(PermissionError):
    """Создание раздела требует действующей роли руководителя или администратора."""


class CatalogSectionNotFound(LookupError):
    """Раздел отсутствует в текущем исходном каталоге Knowledge Service."""


@dataclass(frozen=True, slots=True)
class SectionDistribution:
    """Сообщает результат одноразового монотонного расширения доступа."""

    section_id: UUID
    granted_users: int
    already_distributed: bool


class DistributeSection:
    """Добавляет один новый раздел без смены ролей и прерывания действующих сессий."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        section_catalog: SectionCatalog,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Получает порты каталогов и управляемое время проверки прав."""
        self._unit_of_work = unit_of_work
        self._section_catalog = section_catalog
        self._clock = clock or (lambda: datetime.now(UTC))

    @log_execution_time(operation="identity_catalog_distribution")
    async def execute(
        self, *, section_id: UUID, actor_user_id: UUID
    ) -> SectionDistribution:
        """Проверяет источник раздела до транзакции, затем атомарно выдаёт доступ."""
        if not isinstance(section_id, UUID) or not isinstance(actor_user_id, UUID):
            raise TypeError("section_id и actor_user_id должны быть UUID")
        async with self._unit_of_work() as work:
            actor = await work.users.get_user(actor_user_id)
            await self._require_writer(work.users, actor)
        if section_id not in await self._section_catalog.list_section_ids():
            raise CatalogSectionNotFound(section_id)
        async with self._unit_of_work() as work:
            await work.users.lock_section_catalog()
            users = await work.users.lock_catalog_users(actor_user_id)
            actor = next(
                (user for user in users if user.user_id == actor_user_id), None
            )
            await self._require_writer(work.users, actor)
            now = self._clock()
            reserved = await work.users.reserve_section_distribution(
                section_id, actor_user_id, now
            )
            if not reserved:
                return SectionDistribution(section_id, 0, True)
            granted = 0
            for user in users:
                assignments = await work.users.list_assignments(user.user_id)
                memberships = await work.users.list_memberships(user.user_id)
                roles = effective_roles(user, assignments, memberships, now)
                if Role.PLATFORM_ADMIN in roles or not roles.intersection(
                    {Role.DESIGNER, Role.DEPARTMENT_HEAD}
                ):
                    continue
                existing = await work.users.list_sections(user.user_id)
                if section_id in existing:
                    continue
                await work.users.replace_sections(
                    user.user_id,
                    tuple(sorted({*existing, section_id}, key=str)),
                    actor_user_id=actor_user_id,
                    authorization_version=user.authorization_version,
                    created_at=now,
                )
                granted += 1
            await work.commit()
            return SectionDistribution(section_id, granted, False)

    async def _require_writer(
        self, repository: UserRepository, actor: UserAccount | None
    ) -> None:
        """Повторяет проверку живых полномочий после ожидания блокировок."""
        if actor is None:
            raise UserNotFound("Действующий пользователь не найден")
        assignments = await repository.list_assignments(actor.user_id)
        memberships = await repository.list_memberships(actor.user_id)
        subject = access_subject_for(actor, assignments, memberships, self._clock())
        if Permission.NORMATIVE_WRITE not in effective_permissions(subject):
            raise CatalogWriteRequired("Требуется право создания разделов")
