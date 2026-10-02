# services/user-service/src/pdrd_user_service/application/use_cases/replace_role.py

"""Атомарная замена рабочей роли с проверкой администратора и версии прав."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    UnitOfWorkFactory,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.domain.access import Role
from pdrd_user_service.domain.identity import Membership, UserAccount
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    assign_role,
    effective_roles,
)

WORKER_ROLES = frozenset({Role.DESIGNER, Role.DEPARTMENT_HEAD})


@dataclass(frozen=True, slots=True)
class RoleReplacement:
    """Возвращает профиль и оставшиеся действующие локальные роли."""

    user: UserAccount
    roles: tuple[Role, ...]
    assignments: tuple[RoleAssignment, ...]


class ReplaceWorkerRole:
    """Сохраняет ровно одну рабочую роль либо снимает все рабочие роли."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        """Принимает транзакционный порт и управляемые время/UUID."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = new_id

    async def inspect(
        self, *, actor_user_id: UUID, target_user_id: UUID
    ) -> RoleReplacement:
        """Показывает действующие роли только проверенному администратору."""
        async with self._unit_of_work() as work:
            actor = await work.users.get_user(actor_user_id, for_update=True)
            if actor is None:
                raise AdminRequired("Администратор не найден")
            actor_assignments = await work.users.list_assignments(
                actor_user_id, for_update=True
            )
            now = self._clock()
            if Role.PLATFORM_ADMIN not in effective_roles(
                actor, actor_assignments, (), now
            ):
                raise AdminRequired("Требуется действующая роль администратора")
            target = await work.users.get_user(target_user_id)
            if target is None:
                raise UserNotFound(target_user_id)
            assignments = await work.users.list_assignments(target_user_id)
            memberships = await work.users.list_memberships(target_user_id)
            self._assert_admin_still_active(actor, actor_assignments)
            return self._result(target, assignments, memberships, now)

    async def replace(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        role: Role | None,
        scope: RoleScope | None,
        authorization_version: int,
    ) -> RoleReplacement:
        """Проверяет CAS и записывает отзыв/назначение как один переход."""
        if not isinstance(actor_user_id, UUID) or not isinstance(target_user_id, UUID):
            raise TypeError("Идентификаторы пользователей должны быть UUID")
        if (
            not isinstance(authorization_version, int)
            or isinstance(authorization_version, bool)
            or authorization_version < 1
        ):
            raise ValueError("Требуется корректная версия полномочий")
        if role is None and scope is not None:
            raise ValueError("При снятии роли область должна отсутствовать")
        if role is not None and scope is None:
            raise ValueError("Для назначения роли требуется область")
        if role is not None and role not in WORKER_ROLES:
            raise ValueError("Эта роль назначается отдельной защищённой процедурой")
        async with self._unit_of_work() as work:
            actor = await work.users.get_user(actor_user_id, for_update=True)
            if actor is None:
                raise AdminRequired("Администратор не найден")
            actor_assignments = await work.users.list_assignments(
                actor_user_id, for_update=True
            )
            if Role.PLATFORM_ADMIN not in effective_roles(
                actor, actor_assignments, (), self._clock()
            ):
                raise AdminRequired("Требуется действующая роль администратора")
            target = await work.users.get_user(target_user_id, for_update=True)
            if target is None:
                raise UserNotFound(target_user_id)
            if target.authorization_version != authorization_version:
                raise AuthorizationConflict("Версия прав изменена другим запросом")
            existing = await work.users.list_assignments(
                target_user_id, for_update=True
            )
            now = self._clock()
            if any(
                item.is_active_at(now)
                and (
                    item.role is Role.PLATFORM_ADMIN
                    or item.source is RoleSource.AD_GROUP
                )
                for item in existing
            ):
                raise ValueError(
                    "Управляемая извне или административная роль не меняется"
                )
            previous = tuple(
                item
                for item in existing
                if item.source is RoleSource.LOCAL
                and item.role in WORKER_ROLES
                and item.is_active_at(now)
            )
            memberships = await work.users.list_memberships(target_user_id)
            self._assert_admin_still_active(actor, actor_assignments)
            if (
                len(previous) == 1
                and role == previous[0].role
                and scope == previous[0].scope
            ):
                return self._result(target, existing, memberships, now)
            if not previous and role is None:
                return self._result(target, existing, memberships, now)

            new_assignment = None
            if role is not None and scope is not None:
                new_assignment = RoleAssignment(
                    assignment_id=self._new_id(),
                    user_id=target_user_id,
                    role=role,
                    source=RoleSource.LOCAL,
                    scope=scope,
                    created_at=now,
                )
                # Доменная политика проверяет активность профиля и членство.
                replacement_history = tuple(
                    replace(item, revoked_at=now) if item in previous else item
                    for item in existing
                )
                assign_role(
                    target, new_assignment, replacement_history, memberships, now
                )
            updated = replace(
                target, authorization_version=target.authorization_version + 1
            )
            await work.users.replace_worker_roles(
                updated_user=updated,
                previous_assignment_ids=tuple(item.assignment_id for item in previous),
                new_assignment=new_assignment,
                revoked_at=now,
                expected_authorization_version=target.authorization_version,
                actor_user_id=actor_user_id,
            )
            await work.commit()
            after = tuple(
                replace(item, revoked_at=now) if item in previous else item
                for item in existing
            )
            if new_assignment is not None:
                after = (*after, new_assignment)
            return self._result(updated, after, memberships, now)

    def _assert_admin_still_active(
        self, actor: UserAccount, assignments: tuple[RoleAssignment, ...]
    ) -> None:
        """Повторно проверяет срок роли после чтения целевого профиля."""
        if Role.PLATFORM_ADMIN not in effective_roles(
            actor, assignments, (), self._clock()
        ):
            raise AdminRequired("Роль администратора истекла")

    @staticmethod
    def _result(
        user: UserAccount,
        assignments: tuple[RoleAssignment, ...],
        memberships: tuple[Membership, ...],
        now: datetime,
    ) -> RoleReplacement:
        """Отсекает отозванные назначения и вычисляет доступные роли."""
        active = tuple(item for item in assignments if item.is_active_at(now))
        roles = tuple(sorted(effective_roles(user, active, memberships, now), key=str))
        return RoleReplacement(user=user, roles=roles, assignments=active)
