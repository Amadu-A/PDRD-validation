# services/user-service/src/pdrd_user_service/application/use_cases/review_scope.py

"""Проверяет принадлежность Review автора действующему отделу руководителя.

Gateway сначала устанавливает владельца задания и UUID актёра из проверенной
сессии. Этот сценарий решает только доступ к чужому Review по данным User Service.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from pdrd_user_service.application.ports.repository import UnitOfWorkFactory
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserStatus
from pdrd_user_service.domain.role_assignments import ScopeKind, effective_roles


class ReviewScopeAccess:
    """Сверяет назначение роли и активное членство обоих пользователей."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Получает транзакционный порт и управляемое время проверки."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))

    async def can_read(self, *, actor_user_id: UUID, owner_user_id: UUID) -> bool:
        """Разрешает руководителю только Review участника его действующего отдела."""
        if not isinstance(actor_user_id, UUID) or not isinstance(owner_user_id, UUID):
            raise TypeError("Требуются UUID актёра и владельца")
        async with self._unit_of_work() as work:
            # Одинаковый порядок блокировок не допускает взаимной блокировки
            # при параллельных запросах A→B и B→A.
            ordered = sorted({actor_user_id, owner_user_id})
            profiles = {
                user_id: await work.users.get_user(user_id, for_update=True)
                for user_id in ordered
            }
            actor = profiles[actor_user_id]
            owner = profiles[owner_user_id]
            if actor is None or owner is None:
                return False
            if (
                actor.status is not UserStatus.ACTIVE
                or actor.tier is not AccessTier.MEMBER
                or owner.status is not UserStatus.ACTIVE
                or owner.tier is not AccessTier.MEMBER
            ):
                return False
            now = self._clock()
            assignments = await work.users.list_assignments(
                actor_user_id, for_update=True
            )
            if Role.PLATFORM_ADMIN in effective_roles(actor, assignments, (), now):
                return True
            actor_memberships = await work.users.list_memberships(actor_user_id)
            owner_memberships = await work.users.list_memberships(owner_user_id)
            owner_departments = {
                (item.organization_id, item.department_id)
                for item in owner_memberships
                if item.active and item.department_id is not None
            }
            actor_departments = {
                (item.organization_id, item.department_id)
                for item in actor_memberships
                if item.active and item.department_id is not None
            }
            for assignment in assignments:
                if (
                    assignment.role is Role.DEPARTMENT_HEAD
                    and assignment.is_active_at(now)
                    and assignment.scope.kind is ScopeKind.DEPARTMENT
                ):
                    department = (
                        assignment.scope.organization_id,
                        assignment.scope.department_id,
                    )
                    if (
                        department in actor_departments
                        and department in owner_departments
                    ):
                        return True
            return False
