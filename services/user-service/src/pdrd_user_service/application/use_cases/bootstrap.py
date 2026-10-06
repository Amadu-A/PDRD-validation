# services/user-service/src/pdrd_user_service/application/use_cases/bootstrap.py

"""Однократное назначение первого администратора через серверную команду."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import UnitOfWorkFactory
from pdrd_user_service.application.use_cases.users import UserNotFound
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserKind, UserStatus
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)


async def bootstrap_first_admin(
    unit_of_work: UnitOfWorkFactory,
    user_id: UUID,
    *,
    clock: Callable[[], datetime] | None = None,
    new_id: Callable[[], UUID] = uuid4,
) -> RoleAssignment:
    """Назначает существующему сотруднику первого администратора ровно один раз.

    Долговечный singleton guard и проверку отсутствия другого администратора
    выполняет репозиторий в той же транзакции, что и назначение роли.
    """
    async with unit_of_work() as work:
        user = await work.users.get_user(user_id, for_update=True)
        if user is None:
            raise UserNotFound(user_id)
        if (
            user.kind is not UserKind.CORPORATE
            or user.tier is not AccessTier.MEMBER
            or user.status is not UserStatus.ACTIVE
        ):
            raise ValueError("Первым администратором может быть активный сотрудник")
        now = (clock or (lambda: datetime.now(UTC)))()
        assignment = RoleAssignment(
            assignment_id=new_id(),
            user_id=user.user_id,
            role=Role.PLATFORM_ADMIN,
            source=RoleSource.LOCAL,
            scope=RoleScope(ScopeKind.PLATFORM),
            created_at=now,
        )
        updated = replace(user, authorization_version=user.authorization_version + 1)
        await work.users.bootstrap_admin(
            updated, assignment, user.authorization_version
        )
        await work.commit()
        return assignment
