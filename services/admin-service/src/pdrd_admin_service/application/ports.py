# services/admin-service/src/pdrd_admin_service/application/ports.py

"""Порты для проверки сессии и обращения к каталогу без связи с HTTP-клиентом."""

from typing import Protocol
from uuid import UUID

from pdrd_admin_service.contracts.models import (
    ReplaceRoleRequest,
    RoleDetailResponse,
    SessionIdentity,
    UserPage,
)
from pdrd_admin_service.contracts.organization_models import (
    DepartmentPage,
    DepartmentResponse,
    MembershipChangeResponse,
    MembershipResponse,
    OrganizationPage,
    OrganizationResponse,
)


class SessionVerifier(Protocol):
    """Проверяет браузерный токен только у доверенного auth-service."""

    async def introspect(self, token: str) -> SessionIdentity:
        """Возвращает текущий UUID, права и защиту от CSRF."""
        ...


class UserDirectory(Protocol):
    """Выполняет операции только через авторизованный API user-service."""

    async def list_users(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> UserPage:
        """Читает страницу каталога."""
        ...

    async def get_roles(
        self, actor_user_id: UUID, target_user_id: UUID
    ) -> RoleDetailResponse:
        """Читает проверенные активные назначения выбранного пользователя."""
        ...

    async def replace_role(
        self, actor_user_id: UUID, target_user_id: UUID, command: ReplaceRoleRequest
    ) -> RoleDetailResponse:
        """Передаёт ожидаемую версию для атомарной замены рабочей роли."""
        ...


class OrganizationDirectory(Protocol):
    """Выполняет организационные операции только через API User Service."""

    async def list_organizations(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> OrganizationPage:
        """Читает страницу организаций."""
        ...

    async def create_organization(
        self, actor_user_id: UUID, *, name: str
    ) -> OrganizationResponse:
        """Создаёт организацию."""
        ...

    async def list_departments(
        self, actor_user_id: UUID, organization_id: UUID, *, limit: int, offset: int
    ) -> DepartmentPage:
        """Читает страницу отделов одной организации."""
        ...

    async def create_department(
        self, actor_user_id: UUID, organization_id: UUID, *, name: str
    ) -> DepartmentResponse:
        """Создаёт отдел."""
        ...

    async def list_memberships(
        self, actor_user_id: UUID, target_user_id: UUID
    ) -> tuple[MembershipResponse, ...]:
        """Читает членства выбранного пользователя."""
        ...

    async def set_membership(
        self,
        actor_user_id: UUID,
        target_user_id: UUID,
        organization_id: UUID,
        department_id: UUID,
        *,
        active: bool,
        authorization_version: int,
    ) -> MembershipChangeResponse:
        """Назначает или снимает членство по версии прав."""
        ...
