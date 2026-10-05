# services/user-service/src/pdrd_user_service/application/use_cases/local_superuser.py

"""Первый локальный суперпользователь: профиль, роль и guard в одной транзакции."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import UnitOfWorkFactory
from pdrd_user_service.core.observability import log_execution_time
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import (
    ExternalIdentity,
    UserAccount,
    UserKind,
    UserStatus,
)
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
    effective_roles,
)


class LocalSuperusers:
    """Идемпотентно завершает CLI bootstrap только для прежнего stable key."""

    def __init__(self, unit_of_work: UnitOfWorkFactory) -> None:
        """Получает транзакционный порт собственного каталога."""
        self._unit_of_work = unit_of_work

    @log_execution_time(operation="local_superuser_bootstrap")
    async def create(self, *, subject: UUID, username: str) -> UserAccount:
        """Атомарно создаёт первого администратора; повтор другого ключа запрещён."""
        if (
            not isinstance(subject, UUID)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", username) is None
        ):
            raise ValueError("Некорректная локальная идентичность")
        async with self._unit_of_work() as work:
            key = ("local", "pdrd", str(subject))
            existing = await work.users.find_identity(*key)
            now = datetime.now(UTC)
            if existing is not None:
                existing = await work.users.get_user(existing.user_id, for_update=True)
                if existing is None:
                    raise ValueError("Профиль локального аккаунта исчез")
                assignments = await work.users.list_assignments(
                    existing.user_id, for_update=True
                )
                if (
                    existing.kind is not UserKind.LOCAL
                    or existing.login != username
                    or Role.PLATFORM_ADMIN
                    not in effective_roles(existing, assignments, (), now)
                ):
                    raise ValueError(
                        "Bootstrap относится к другому или неактивному профилю"
                    )
                return existing
            user = UserAccount(
                user_id=uuid4(),
                kind=UserKind.LOCAL,
                tier=AccessTier.MEMBER,
                status=UserStatus.ACTIVE,
                display_name=username,
                login=username,
                created_at=now,
            )
            await work.users.create_user(user, ExternalIdentity(*key, user.user_id))
            assignment = RoleAssignment(
                assignment_id=uuid4(),
                user_id=user.user_id,
                role=Role.PLATFORM_ADMIN,
                source=RoleSource.LOCAL,
                scope=RoleScope(ScopeKind.PLATFORM),
                created_at=now,
            )
            updated = replace(user, authorization_version=2)
            await work.users.bootstrap_admin(updated, assignment, 1)
            await work.commit()
            return updated
