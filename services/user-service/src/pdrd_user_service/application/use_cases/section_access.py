# services/user-service/src/pdrd_user_service/application/use_cases/section_access.py

"""Читает актуальные назначения разделов и проверяет пользователя в своей БД."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pdrd_user_service.application.ports.repository import UnitOfWorkFactory
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.core.observability import log_execution_time
from pdrd_user_service.domain.access import Role
from pdrd_user_service.domain.identity import UserStatus
from pdrd_user_service.domain.role_assignments import effective_roles


@dataclass(frozen=True, slots=True)
class SectionAccessSnapshot:
    """Фиксирует действующие разделы, версию прав и платформенную область."""

    user_id: UUID
    section_ids: tuple[UUID, ...]
    all_sections: bool
    authorization_version: int


class UserSections:
    """Сверяет профиль и администратора до выдачи назначенного набора UUID."""

    def __init__(self, unit_of_work: UnitOfWorkFactory) -> None:
        """Принимает транзакционный порт собственного сервиса."""
        self._unit_of_work = unit_of_work

    @log_execution_time(operation="identity_section_access_read")
    async def read(
        self, *, actor_user_id: UUID, target_user_id: UUID
    ) -> SectionAccessSnapshot:
        """Разрешает чтение себе или администратору; заблокированный профиль не проходит."""
        async with self._unit_of_work() as work:
            profiles = {
                item: await work.users.get_user(item, for_update=True)
                for item in sorted({actor_user_id, target_user_id})
            }
            actor = profiles[actor_user_id]
            target = profiles[target_user_id]
            if actor is None or actor.status is not UserStatus.ACTIVE:
                raise AdminRequired("Требуется действующий профиль")
            if target is None:
                raise UserNotFound(target_user_id)
            now = datetime.now(UTC)
            actor_roles = effective_roles(
                actor,
                await work.users.list_assignments(actor_user_id, for_update=True),
                (),
                now,
            )
            if (
                actor_user_id != target_user_id
                and Role.PLATFORM_ADMIN not in actor_roles
            ):
                raise AdminRequired("Требуется администратор")
            target_roles = effective_roles(
                target,
                await work.users.list_assignments(target_user_id, for_update=True),
                (),
                now,
            )
            sections = (
                await work.users.list_sections(target_user_id)
                if target.status is UserStatus.ACTIVE
                else ()
            )
            return SectionAccessSnapshot(
                target_user_id,
                sections,
                Role.PLATFORM_ADMIN in target_roles,
                target.authorization_version,
            )
