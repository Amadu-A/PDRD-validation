# services/user-service/src/pdrd_user_service/application/use_cases/users.py

"""Сценарии внутреннего каталога пользователей и управляемых ролей.

Сервисный ключ подтверждает вызывающий сервис, а роль действующего человека
проверяется заново по профилю и назначениям в базе User Service.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import (
    IdentityConflict,
    UnitOfWorkFactory,
    UserRepository,
)
from pdrd_user_service.domain.access import (
    AccessTier,
    Permission,
    Role,
    effective_permissions,
)
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
    access_subject_for,
    assign_role,
    effective_roles,
    revoke_role,
)


class UserNotFound(LookupError):
    """Запрошенный пользователь не найден."""


class AdminRequired(PermissionError):
    """Действующий субъект не является активным администратором платформы."""


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    """Версия и операционные права, которые ещё требуют проверки ресурса."""

    user_id: UUID
    authorization_version: int
    tier: AccessTier
    status: UserStatus
    roles: tuple[Role, ...]
    permissions: tuple[Permission, ...]


@dataclass(frozen=True, slots=True)
class RoleChange:
    """Результат транзакционного назначения или отзыва роли."""

    user: UserAccount
    assignment: RoleAssignment


class UserDirectory:
    """Применяет доменную политику к сохранённым профилям и назначениям."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        """Получает транзакционный порт и управляемые источники времени/UUID."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = new_id

    async def get_user(self, user_id: UUID) -> UserAccount:
        """Читает профиль по внутреннему UUID без поиска по email или логину."""
        async with self._unit_of_work() as work:
            user = await work.users.get_user(user_id)
            if user is None:
                raise UserNotFound(user_id)
            return user

    async def find_identity(
        self, provider_id: str, namespace: str, subject: str
    ) -> UserAccount | None:
        """Ищет профиль строго по полному устойчивому ключу провайдера."""
        async with self._unit_of_work() as work:
            return await work.users.find_identity(provider_id, namespace, subject)

    async def provision(
        self,
        *,
        provider_id: str,
        namespace: str,
        subject: str,
        kind: UserKind,
        display_name: str,
        login: str | None = None,
        email: str | None = None,
    ) -> UserAccount:
        """Создаёт активный профиль после подтверждения личности вызывающим auth-service.

        Повторный запрос с тем же stable key возвращает прежний профиль. Ни email,
        ни login не используются для привязки к существующему пользователю.
        """
        # Повтор не создаёт UUID и не валидирует изменившийся профиль.
        # Пустой ключ всё равно никогда не поступает в репозиторий.
        if any(
            not isinstance(part, str) or not part.strip()
            for part in (provider_id, namespace, subject)
        ):
            raise ValueError("Устойчивая идентичность должна быть полной")
        if kind is UserKind.EXTERNAL and provider_id.strip().casefold() == "email":
            raise ValueError(
                "Email-профиль создаётся через регистрацию с подтверждением"
            )
        try:
            async with self._unit_of_work() as work:
                existing = await work.users.find_identity(
                    provider_id, namespace, subject
                )
                if existing is not None:
                    if existing.kind is not kind:
                        raise IdentityConflict("Тип учётной записи не совпадает")
                    return existing
                identity_key = ExternalIdentity(
                    provider_id, namespace, subject, self._new_id()
                )
                user = UserAccount(
                    user_id=identity_key.user_id,
                    kind=kind,
                    tier=AccessTier.MEMBER
                    if kind is UserKind.CORPORATE
                    else AccessTier.REGISTERED_FREE,
                    status=UserStatus.ACTIVE,
                    display_name=display_name,
                    created_at=self._clock(),
                    login=login,
                    email=email,
                )
                await work.users.create_user(user, identity_key)
                await work.commit()
                return user
        except IdentityConflict:
            # Одновременные первые входы могли выиграть одну и ту же уникальную
            # внешнюю идентичность; повторное чтение идёт в новой транзакции.
            existing = await self.find_identity(provider_id, namespace, subject)
            if existing is None:
                raise
            if existing.kind is not kind:
                raise IdentityConflict("Тип учётной записи не совпадает") from None
            return existing

    async def permissions(self, user_id: UUID) -> PermissionSnapshot:
        """Возвращает актуальные операционные права и версию их пересмотра."""
        async with self._unit_of_work() as work:
            user = await work.users.get_user(user_id)
            if user is None:
                raise UserNotFound(user_id)
            assignments = await work.users.list_assignments(user_id)
            memberships = await work.users.list_memberships(user_id)
            subject = access_subject_for(user, assignments, memberships, self._clock())
            return PermissionSnapshot(
                user_id=user.user_id,
                authorization_version=user.authorization_version,
                tier=user.tier,
                status=user.status,
                roles=tuple(sorted(subject.roles, key=str)),
                permissions=tuple(sorted(effective_permissions(subject), key=str)),
            )

    async def assign(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        role: Role,
        scope: RoleScope,
        expires_at: datetime | None = None,
    ) -> RoleChange:
        """Назначает локальную роль после чтения действующей роли администратора."""
        async with self._unit_of_work() as work:
            actor, actor_assignments = await self._load_admin(work.users, actor_user_id)
            self._assert_admin(actor, actor_assignments, self._clock())
            target = await work.users.get_user(target_user_id, for_update=True)
            if target is None:
                raise UserNotFound(target_user_id)
            existing = await work.users.list_assignments(
                target_user_id, for_update=True
            )
            memberships = await work.users.list_memberships(target_user_id)
            now = self._clock()
            self._assert_admin(actor, actor_assignments, now)
            assignment = RoleAssignment(
                assignment_id=self._new_id(),
                user_id=target_user_id,
                role=role,
                source=RoleSource.LOCAL,
                scope=scope,
                created_at=now,
                expires_at=expires_at,
            )
            updated, _ = assign_role(target, assignment, existing, memberships, now)
            await work.users.add_role_assignment(
                updated, assignment, target.authorization_version, actor_user_id
            )
            await work.commit()
            return RoleChange(updated, assignment)

    async def revoke(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        assignment_id: UUID,
    ) -> RoleChange:
        """Отзывает локальную роль и увеличивает версию полномочий пользователя."""
        async with self._unit_of_work() as work:
            actor, actor_assignments = await self._load_admin(work.users, actor_user_id)
            self._assert_admin(actor, actor_assignments, self._clock())
            target = await work.users.get_user(target_user_id, for_update=True)
            if target is None:
                raise UserNotFound(target_user_id)
            existing = await work.users.list_assignments(
                target_user_id, for_update=True
            )
            now = self._clock()
            self._assert_admin(actor, actor_assignments, now)
            updated, assignments = revoke_role(target, assignment_id, existing, now)
            assignment = next(
                item for item in assignments if item.assignment_id == assignment_id
            )
            await work.users.revoke_role_assignment(
                updated, assignment_id, now, target.authorization_version, actor_user_id
            )
            await work.commit()
            return RoleChange(updated, assignment)

    @staticmethod
    async def _load_admin(
        users: UserRepository, actor_user_id: UUID
    ) -> tuple[UserAccount, tuple[RoleAssignment, ...]]:
        """Блокирует профиль и историю ролей администратора до конца транзакции."""
        actor = await users.get_user(actor_user_id, for_update=True)
        if actor is None:
            raise AdminRequired("Администратор не найден")
        assignments = await users.list_assignments(actor_user_id, for_update=True)
        return actor, assignments

    @staticmethod
    def _assert_admin(
        actor: UserAccount, assignments: tuple[RoleAssignment, ...], now: datetime
    ) -> None:
        """Проверяет активную роль на момент действия, после ожидания DB lock."""
        if Role.PLATFORM_ADMIN not in effective_roles(actor, assignments, (), now):
            raise AdminRequired("Требуется действующая роль администратора")
