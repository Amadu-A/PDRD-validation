# services/user-service/src/pdrd_user_service/application/use_cases/public_profiles.py

"""Читает ограниченный справочник авторов Experience с актуальными ролями."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pdrd_user_service.application.ports.repository import UnitOfWorkFactory
from pdrd_user_service.core.observability import log_execution_time
from pdrd_user_service.domain.access import Permission, Role, effective_permissions
from pdrd_user_service.domain.role_assignments import access_subject_for


class ProfileReadDenied(PermissionError):
    """Текущий профиль не имеет права читать каталог авторов."""


@dataclass(frozen=True, slots=True)
class PublicProfile:
    """Открывает логин, отображаемое имя и роли без секретов и служебных полей."""

    user_id: UUID
    login: str | None
    display_name: str
    roles: tuple[Role, ...]


class PublicProfiles:
    """Проверяет живые права вызывающего пользователя перед пакетным чтением."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Принимает собственное хранилище и управляемое время проверки ролей."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))

    @log_execution_time(operation="identity_public_profiles_read")
    async def read(
        self, *, actor_user_id: UUID, user_ids: tuple[UUID, ...]
    ) -> tuple[PublicProfile, ...]:
        """Возвращает только запрошенные существующие профили; повторы UUID объединяет."""
        if not isinstance(actor_user_id, UUID) or not 1 <= len(user_ids) <= 100:
            raise ValueError("Требуется от одного до ста UUID профилей")
        if any(not isinstance(item, UUID) for item in user_ids):
            raise ValueError("Идентификаторы профилей должны быть UUID")
        async with self._unit_of_work() as work:
            actor = await work.users.get_user(actor_user_id)
            if actor is None:
                raise ProfileReadDenied("Требуется действующий профиль")
            now = self._clock()
            actor_subject = access_subject_for(
                actor,
                await work.users.list_assignments(actor_user_id),
                await work.users.list_memberships(actor_user_id),
                now,
            )
            if Permission.EXPERIENCE_CATALOG_READ not in effective_permissions(
                actor_subject
            ):
                raise ProfileReadDenied("Нет доступа к каталогу авторов")
            result = []
            for user_id in dict.fromkeys(user_ids):
                user = (
                    actor
                    if user_id == actor_user_id
                    else await work.users.get_user(user_id)
                )
                if user is None:
                    continue
                subject = (
                    actor_subject
                    if user_id == actor_user_id
                    else access_subject_for(
                        user,
                        await work.users.list_assignments(user_id),
                        await work.users.list_memberships(user_id),
                        now,
                    )
                )
                result.append(
                    PublicProfile(
                        user_id=user.user_id,
                        login=user.login or user.email,
                        display_name=user.display_name,
                        roles=tuple(sorted(subject.roles, key=str)),
                    )
                )
            return tuple(result)
