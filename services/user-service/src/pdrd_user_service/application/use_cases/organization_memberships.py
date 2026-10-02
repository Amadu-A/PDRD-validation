# services/user-service/src/pdrd_user_service/application/use_cases/organization_memberships.py

"""Управляет организациями, отделами и членством через собственную БД User Service.

Каждая операция заново проверяет роль администратора в этой же транзакции.
Изменение членства повышает версию полномочий, чтобы старый снимок прав не жил дальше.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    UnitOfWorkFactory,
    UserRepository,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import (
    Department,
    Membership,
    Organization,
    UserStatus,
)
from pdrd_user_service.domain.role_assignments import effective_roles


class OrganizationNotFound(Exception):
    """Организация не существует."""


class DepartmentNotFound(Exception):
    """Отдел не существует в указанной организации."""


class MembershipNotFound(Exception):
    """Членство, которое требуется отключить, не найдено."""


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """Параметры и итог ограниченного списка справочника."""

    items: tuple[Organization | Department, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class MembershipChange:
    """Новая версия прав и точное состояние одного членства."""

    user_id: UUID
    authorization_version: int
    membership: Membership


class OrganizationMemberships:
    """Реализует административные команды без доступа admin-service к SQL."""

    def __init__(
        self,
        unit_of_work: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        """Получает транзакции и управляемые источники времени и UUID."""
        self._unit_of_work = unit_of_work
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_id = new_id

    async def _require_admin(
        self, repository: UserRepository, actor_user_id: UUID
    ) -> None:
        """Проверяет действующую роль до чтения или записи чужих данных."""
        actor = await repository.get_user(actor_user_id, for_update=True)
        if actor is None:
            raise AdminRequired("Администратор не найден")
        assignments = await repository.list_assignments(actor_user_id, for_update=True)
        if Role.PLATFORM_ADMIN not in effective_roles(
            actor, assignments, (), self._clock()
        ):
            raise AdminRequired("Требуется действующая роль администратора")

    @staticmethod
    def _page_bounds(limit: int, offset: int) -> None:
        """Ограничивает объём ответа до обращения к БД."""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Некорректные параметры страницы")

    async def organizations(
        self, *, actor_user_id: UUID, limit: int, offset: int
    ) -> CatalogPage:
        """Читает страницу организаций для авторизованного администратора."""
        self._page_bounds(limit, offset)
        async with self._unit_of_work() as work:
            await self._require_admin(work.users, actor_user_id)
            items, total = await work.users.list_organizations(
                limit=limit, offset=offset
            )
            return CatalogPage(items, total, limit, offset)

    async def create_organization(
        self, *, actor_user_id: UUID, name: str
    ) -> Organization:
        """Создаёт активную организацию после проверки роли."""
        organization = Organization(self._new_id(), name.strip())
        async with self._unit_of_work() as work:
            await self._require_admin(work.users, actor_user_id)
            await work.users.create_organization(organization)
            await work.commit()
        return organization

    async def departments(
        self, *, actor_user_id: UUID, organization_id: UUID, limit: int, offset: int
    ) -> CatalogPage:
        """Читает только отделы указанной организации."""
        self._page_bounds(limit, offset)
        async with self._unit_of_work() as work:
            await self._require_admin(work.users, actor_user_id)
            if await work.users.get_organization(organization_id) is None:
                raise OrganizationNotFound
            items, total = await work.users.list_departments(
                organization_id, limit=limit, offset=offset
            )
            return CatalogPage(items, total, limit, offset)

    async def create_department(
        self, *, actor_user_id: UUID, organization_id: UUID, name: str
    ) -> Department:
        """Создаёт отдел внутри действующей организации."""
        department = Department(self._new_id(), organization_id, name.strip())
        async with self._unit_of_work() as work:
            await self._require_admin(work.users, actor_user_id)
            organization = await work.users.get_organization(organization_id)
            if organization is None or not organization.active:
                raise OrganizationNotFound
            await work.users.create_department(department)
            await work.commit()
        return department

    async def memberships(
        self, *, actor_user_id: UUID, target_user_id: UUID
    ) -> tuple[Membership, ...]:
        """Показывает членства выбранного профиля после проверки администратора."""
        async with self._unit_of_work() as work:
            await self._require_admin(work.users, actor_user_id)
            if await work.users.get_user(target_user_id) is None:
                raise UserNotFound(target_user_id)
            return await work.users.list_memberships(target_user_id)

    async def set_membership(
        self,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        organization_id: UUID,
        department_id: UUID,
        active: bool,
        authorization_version: int,
    ) -> MembershipChange:
        """Сравнивает версию и назначает либо снимает членство в отделе."""
        if (
            not isinstance(authorization_version, int)
            or isinstance(authorization_version, bool)
            or authorization_version < 1
        ):
            raise ValueError("Требуется корректная версия полномочий")
        async with self._unit_of_work() as work:
            await self._require_admin(work.users, actor_user_id)
            target = await work.users.get_user(target_user_id, for_update=True)
            if target is None:
                raise UserNotFound(target_user_id)
            if target.authorization_version != authorization_version:
                raise AuthorizationConflict("Версия прав изменилась")
            if (
                target.status is not UserStatus.ACTIVE
                or target.tier is not AccessTier.MEMBER
            ):
                raise ValueError("Членство доступно только активному участнику")
            organization = await work.users.get_organization(organization_id)
            department = await work.users.get_department(organization_id, department_id)
            if organization is None or department is None:
                raise DepartmentNotFound
            if active and (not organization.active or not department.active):
                raise ValueError("Организация и отдел должны быть активны")
            previous = await work.users.get_department_membership(
                target_user_id, organization_id, department_id
            )
            if previous is None and not active:
                raise MembershipNotFound
            if previous is not None and previous.active is active:
                return MembershipChange(target_user_id, authorization_version, previous)
            membership = Membership(
                target_user_id, organization_id, department_id, active
            )
            updated = replace(
                target, authorization_version=target.authorization_version + 1
            )
            await work.users.set_department_membership(
                updated,
                membership,
                expected_authorization_version=target.authorization_version,
            )
            await work.commit()
            return MembershipChange(
                target_user_id, updated.authorization_version, membership
            )
