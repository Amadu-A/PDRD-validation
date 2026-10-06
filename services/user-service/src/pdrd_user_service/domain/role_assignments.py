# services/user-service/src/pdrd_user_service/domain/role_assignments.py

"""Назначения ролей с источником, областью действия и отзывом прав.

Правила не обращаются к AD или БД. Прикладной слой должен передавать только
проверенные назначения и сохранять изменение назначения вместе с новой
версией полномочий пользователя в одной транзакции.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pdrd_user_service.domain.access import AccessSubject, AccessTier, Role
from pdrd_user_service.domain.identity import Membership, UserAccount, UserStatus


class RoleSource(StrEnum):
    """Показывает источник; AD-группы не дают права до проверенной синхронизации."""

    LOCAL = "local"
    AD_GROUP = "ad_group"


class ScopeKind(StrEnum):
    """Ограничивает назначение платформой, организацией, отделом или владельцем."""

    PLATFORM = "platform"
    ORGANIZATION = "organization"
    DEPARTMENT = "department"
    OWN = "own"
    SECTIONS = "sections"


@dataclass(frozen=True, slots=True)
class RoleScope:
    """Хранит точный идентификатор области назначения роли."""

    kind: ScopeKind
    organization_id: UUID | None = None
    department_id: UUID | None = None

    def __post_init__(self) -> None:
        """Запрещает неполную область или лишний идентификатор."""
        if not isinstance(self.kind, ScopeKind):
            raise TypeError("kind должен быть ScopeKind")
        if self.kind in {ScopeKind.PLATFORM, ScopeKind.OWN, ScopeKind.SECTIONS}:
            if self.organization_id is not None or self.department_id is not None:
                raise ValueError("Глобальная и собственная области не имеют ID")
        elif self.kind is ScopeKind.ORGANIZATION:
            if (
                not isinstance(self.organization_id, UUID)
                or self.department_id is not None
            ):
                raise ValueError("Для организации нужен только organization_id")
        elif not isinstance(self.organization_id, UUID) or not isinstance(
            self.department_id, UUID
        ):
            raise ValueError("Для отдела нужны organization_id и department_id")


@dataclass(frozen=True, slots=True)
class ResourceScope:
    """Описывает владельца и организационную область конкретного ресурса."""

    owner_user_id: UUID
    organization_id: UUID | None = None
    department_id: UUID | None = None

    def __post_init__(self) -> None:
        """Не позволяет подменить владельца или указать отдел без организации."""
        if not isinstance(self.owner_user_id, UUID):
            raise TypeError("owner_user_id должен быть UUID")
        if self.organization_id is not None and not isinstance(
            self.organization_id, UUID
        ):
            raise TypeError("organization_id должен быть UUID")
        if self.department_id is not None:
            if not isinstance(self.department_id, UUID):
                raise TypeError("department_id должен быть UUID")
            if self.organization_id is None:
                raise ValueError("Ресурс отдела требует organization_id")


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    """Фиксирует одну роль, источник и срок действия для одного пользователя."""

    assignment_id: UUID
    user_id: UUID
    role: Role
    source: RoleSource
    scope: RoleScope
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    external_group_id: str | None = None

    def __post_init__(self) -> None:
        """Отвергает неверные идентификаторы, времена и внешний источник."""
        if not isinstance(self.assignment_id, UUID) or not isinstance(
            self.user_id, UUID
        ):
            raise TypeError("Идентификаторы назначения должны быть UUID")
        if not isinstance(self.role, Role) or not isinstance(self.source, RoleSource):
            raise TypeError("Роль и её источник должны быть перечислениями")
        if not isinstance(self.scope, RoleScope):
            raise TypeError("scope должен быть RoleScope")
        _require_aware_time(self.created_at)
        if self.expires_at is not None:
            _require_aware_time(self.expires_at)
            if self.expires_at <= self.created_at:
                raise ValueError("Срок роли должен быть позднее создания")
        if self.revoked_at is not None:
            _require_aware_time(self.revoked_at)
            if self.revoked_at < self.created_at:
                raise ValueError("Отзыв не может предшествовать созданию")
        if self.source is RoleSource.AD_GROUP:
            if not self.external_group_id or not self.external_group_id.strip():
                raise ValueError("Для AD-группы нужен устойчивый внешний ID")
        elif self.external_group_id is not None:
            raise ValueError("Локальное назначение не содержит AD group ID")
        if self.role is Role.PLATFORM_ADMIN and (
            self.scope.kind is not ScopeKind.PLATFORM
            or self.source is not RoleSource.LOCAL
        ):
            raise ValueError("Администратор платформы пока назначается локально")
        if self.role is Role.DEPARTMENT_HEAD and self.scope.kind not in {
            ScopeKind.DEPARTMENT,
            ScopeKind.SECTIONS,
        }:
            raise ValueError(
                "Руководитель ограничен назначенными разделами или прежним отделом"
            )
        if self.role is Role.DESIGNER and self.scope.kind is not ScopeKind.OWN:
            raise ValueError("Роль проектировщика ограничена собственными ресурсами")

    def is_active_at(self, at: datetime) -> bool:
        """Проверяет срок назначения и отзыв на заданный момент времени."""
        _require_aware_time(at)
        return (
            self.created_at <= at
            and (self.revoked_at is None or at < self.revoked_at)
            and (self.expires_at is None or at < self.expires_at)
        )


def _require_aware_time(value: datetime) -> None:
    """Требует дату с часовым поясом для однозначной проверки срока прав."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Время назначения должно содержать timezone")
    if value.utcoffset() is None:
        raise ValueError("Время назначения должно содержать timezone")


def _matches_membership(
    scope: RoleScope,
    user_id: UUID,
    memberships: Iterable[Membership],
) -> bool:
    """Проверяет принадлежность пользователя ровно указанной области."""
    if scope.kind in {ScopeKind.PLATFORM, ScopeKind.OWN, ScopeKind.SECTIONS}:
        return True
    return any(
        membership.active
        and membership.user_id == user_id
        and membership.organization_id == scope.organization_id
        and (
            scope.kind is ScopeKind.ORGANIZATION
            or membership.department_id == scope.department_id
        )
        for membership in memberships
    )


def effective_roles(
    user: UserAccount,
    assignments: Iterable[RoleAssignment],
    memberships: Iterable[Membership],
    at: datetime,
) -> frozenset[Role]:
    """Собирает роли только действующего участника в разрешённых областях."""
    _require_aware_time(at)
    if user.status is not UserStatus.ACTIVE or user.tier is not AccessTier.MEMBER:
        return frozenset()
    membership_snapshot = tuple(memberships)
    return frozenset(
        assignment.role
        for assignment in assignments
        if assignment.user_id == user.user_id
        and assignment.source is RoleSource.LOCAL
        and assignment.is_active_at(at)
        and _matches_membership(assignment.scope, user.user_id, membership_snapshot)
    )


def has_role_for_resource(
    user: UserAccount,
    assignments: Iterable[RoleAssignment],
    memberships: Iterable[Membership],
    role: Role,
    resource: ResourceScope,
    at: datetime,
) -> bool:
    """Проверяет конкретную роль в области ресурса, не заменяя permission check."""
    _require_aware_time(at)
    if not isinstance(role, Role):
        raise TypeError("role должен быть Role")
    if not isinstance(resource, ResourceScope):
        raise TypeError("resource должен быть ResourceScope")
    if user.status is not UserStatus.ACTIVE or user.tier is not AccessTier.MEMBER:
        return False

    membership_snapshot = tuple(memberships)
    for assignment in assignments:
        if (
            assignment.user_id != user.user_id
            or assignment.role is not role
            or assignment.source is not RoleSource.LOCAL
            or not assignment.is_active_at(at)
            or not _matches_membership(
                assignment.scope, user.user_id, membership_snapshot
            )
        ):
            continue

        scope = assignment.scope
        if scope.kind is ScopeKind.PLATFORM:
            return True
        if scope.kind is ScopeKind.OWN and resource.owner_user_id == user.user_id:
            if resource.organization_id is None:
                return True
            if any(
                membership.active
                and membership.user_id == user.user_id
                and membership.organization_id == resource.organization_id
                and (
                    resource.department_id is None
                    or membership.department_id == resource.department_id
                )
                for membership in membership_snapshot
            ):
                return True
        if (
            scope.kind is ScopeKind.ORGANIZATION
            and resource.organization_id == scope.organization_id
        ):
            return True
        if (
            scope.kind is ScopeKind.DEPARTMENT
            and resource.organization_id == scope.organization_id
            and resource.department_id == scope.department_id
        ):
            return True
    return False


def access_subject_for(
    user: UserAccount,
    assignments: Iterable[RoleAssignment],
    memberships: Iterable[Membership],
    at: datetime,
) -> AccessSubject:
    """Строит операционные права из текущего профиля и назначений сервера."""
    roles = effective_roles(user, assignments, memberships, at)
    return AccessSubject(
        tier=user.tier,
        roles=roles,
        active=user.status is UserStatus.ACTIVE,
        review_access_enabled=user.review_access_enabled,
    )


def assign_role(
    user: UserAccount,
    assignment: RoleAssignment,
    existing: Iterable[RoleAssignment],
    memberships: Iterable[Membership],
    at: datetime,
) -> tuple[UserAccount, tuple[RoleAssignment, ...]]:
    """Проверяет назначение и увеличивает версию полномочий до сохранения.

    Возвращённые профиль и назначения должны сохраняться атомарно. Проверка
    полномочий администратора или bootstrap выполняется вызывающим use-case.
    """
    _require_aware_time(at)
    if user.status is not UserStatus.ACTIVE or user.tier is not AccessTier.MEMBER:
        raise ValueError("Назначать роль можно только активному участнику")
    if assignment.role is Role.PLATFORM_ADMIN:
        raise ValueError("Администратора назначает отдельный защищённый процесс")
    if assignment.source is not RoleSource.LOCAL:
        raise ValueError("AD-группа требует отдельной проверенной синхронизации")
    if assignment.user_id != user.user_id or not assignment.is_active_at(at):
        raise ValueError("Назначение относится к другому пользователю или сроку")
    if not _matches_membership(assignment.scope, user.user_id, memberships):
        raise ValueError("Пользователь не входит в область назначения")
    current = tuple(existing)
    if any(item.assignment_id == assignment.assignment_id for item in current):
        raise ValueError("Идентификатор назначения уже существует")
    if any(
        item.user_id == user.user_id
        and item.role == assignment.role
        and item.source == assignment.source
        and item.scope == assignment.scope
        and item.external_group_id == assignment.external_group_id
        and item.is_active_at(at)
        for item in current
    ):
        raise ValueError("Такое назначение уже действует")
    return (
        replace(user, authorization_version=user.authorization_version + 1),
        (*current, assignment),
    )


def revoke_role(
    user: UserAccount,
    assignment_id: UUID,
    existing: Iterable[RoleAssignment],
    at: datetime,
) -> tuple[UserAccount, tuple[RoleAssignment, ...]]:
    """Отзывает собственное назначение и увеличивает версию полномочий."""
    _require_aware_time(at)
    current = tuple(existing)
    if not any(
        item.assignment_id == assignment_id
        and item.user_id == user.user_id
        and item.revoked_at is None
        and item.is_active_at(at)
        for item in current
    ):
        raise ValueError("Действующее назначение не найдено")
    if any(
        item.assignment_id == assignment_id
        and item.user_id == user.user_id
        and item.role is Role.PLATFORM_ADMIN
        for item in current
    ):
        raise ValueError("Администратора отзывает отдельный защищённый процесс")
    updated = tuple(
        replace(item, revoked_at=at)
        if item.assignment_id == assignment_id and item.user_id == user.user_id
        else item
        for item in current
    )
    return replace(user, authorization_version=user.authorization_version + 1), updated
