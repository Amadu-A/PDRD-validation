# services/user-service/src/pdrd_user_service/application/use_cases/equipment_sources_access.py

"""Выдаёт и отзывает право управления источниками оборудования с повторной проверкой администратора."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    UnitOfWorkFactory,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.core.observability import log_execution_time
from pdrd_user_service.domain.access import Role
from pdrd_user_service.domain.equipment_sources_access import (
    EquipmentSourcesAccessState,
    equipment_sources_access_state,
)
from pdrd_user_service.domain.identity import UserAccount
from pdrd_user_service.domain.role_assignments import (
    access_subject_for,
    effective_roles,
)


@dataclass(frozen=True, slots=True)
class EquipmentSourcesAccessChange:
    """Передаёт сохранённый профиль и вычисленное состояние галочки."""

    user: UserAccount
    state: EquipmentSourcesAccessState


class EquipmentSourcesAccessManagement:
    """Меняет только ручной доступ пользователя, не подменяя его роль."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Получает транзакции и управляемое время для проверки сроков ролей."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))

    @log_execution_time(operation="identity_equipment_sources_access_change")
    async def change(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        enabled: bool,
        authorization_version: int,
    ) -> EquipmentSourcesAccessChange:
        """Выдаёт или отзывает право управления источниками оборудования для активного пользователя.

        Проверяет живую роль администратора, блокирует профили в едином порядке
        UUID и сравнивает версию прав. Флаг, новая версия и аудит сохраняются
        одной транзакцией; повтор того же значения ничего не меняет.
        Автоматические права администратора не отзываются.
        """
        if not isinstance(actor_user_id, UUID) or not isinstance(target_user_id, UUID):
            raise TypeError("Актёр и целевой профиль должны быть UUID")
        if not isinstance(enabled, bool):
            raise TypeError("enabled должен быть bool")
        if type(authorization_version) is not int or authorization_version < 1:
            raise ValueError("Некорректная версия прав")
        async with self._unit_of_work() as work:
            accounts = {
                user_id: await work.users.get_user(user_id, for_update=True)
                for user_id in sorted({actor_user_id, target_user_id}, key=str)
            }
            actor = accounts[actor_user_id]
            at = self._clock()
            if actor is None or Role.PLATFORM_ADMIN not in effective_roles(
                actor,
                await work.users.list_assignments(actor_user_id, for_update=True),
                (),
                at,
            ):
                raise AdminRequired("Требуется действующая роль администратора")
            target = accounts[target_user_id]
            if target is None:
                raise UserNotFound("Профиль не найден")
            if target.authorization_version != authorization_version:
                raise AuthorizationConflict("Версия прав изменилась")
            assignments = await work.users.list_assignments(
                target_user_id, for_update=True
            )
            memberships = await work.users.list_memberships(target_user_id)
            state = equipment_sources_access_state(
                access_subject_for(target, assignments, memberships, at)
            )
            if state.equipment_sources_access_automatic:
                if not enabled:
                    raise ValueError(
                        "Для администратора управление источниками оборудования доступно автоматически"
                    )
                return EquipmentSourcesAccessChange(target, state)
            if not state.equipment_sources_access_editable:
                raise ValueError(
                    "Назначение права управления источниками оборудования доступно только активному пользователю"
                )
            if target.equipment_sources_access_enabled == enabled:
                return EquipmentSourcesAccessChange(target, state)
            updated = replace(
                target,
                equipment_sources_access_enabled=enabled,
                authorization_version=authorization_version + 1,
            )
            await work.users.replace_equipment_sources_access(
                updated,
                expected_authorization_version=authorization_version,
                actor_user_id=actor_user_id,
                created_at=at,
            )
            await work.commit()
            return EquipmentSourcesAccessChange(
                updated,
                equipment_sources_access_state(
                    access_subject_for(updated, assignments, memberships, at)
                ),
            )
