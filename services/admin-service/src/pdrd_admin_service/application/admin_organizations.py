# services/admin-service/src/pdrd_admin_service/application/admin_organizations.py

"""Оркестрирует организационные операции без прямого доступа к таблицам User Service."""

from uuid import UUID

from pdrd_admin_service.application.ports import OrganizationDirectory, SessionVerifier
from pdrd_admin_service.application.session_authorization import authorize_session
from pdrd_admin_service.contracts.organization_models import (
    DepartmentPage,
    DepartmentResponse,
    MembershipChangeResponse,
    MembershipResponse,
    OrganizationPage,
    OrganizationResponse,
)


class AdminOrganizations:
    """Передаёт user-service только UUID из проверенной сессии администратора."""

    def __init__(
        self, sessions: SessionVerifier, organizations: OrganizationDirectory
    ) -> None:
        """Получает доверенные порты сессии и собственного каталога пользователей."""
        self._sessions = sessions
        self._organizations = organizations

    async def list_organizations(
        self, token: str | None, *, limit: int, offset: int
    ) -> OrganizationPage:
        """Показывает страницу организаций действующему администратору."""
        actor = await authorize_session(self._sessions, token, "admin.access")
        return await self._organizations.list_organizations(
            actor.user_id, limit=limit, offset=offset
        )

    async def create_organization(
        self, token: str | None, *, name: str, csrf: str | None
    ) -> OrganizationResponse:
        """Создаёт организацию с проверкой права и CSRF."""
        actor = await authorize_session(
            self._sessions, token, "users.roles.assign", csrf=csrf or ""
        )
        return await self._organizations.create_organization(actor.user_id, name=name)

    async def list_departments(
        self, token: str | None, organization_id: UUID, *, limit: int, offset: int
    ) -> DepartmentPage:
        """Показывает отделы одной организации."""
        actor = await authorize_session(self._sessions, token, "admin.access")
        return await self._organizations.list_departments(
            actor.user_id, organization_id, limit=limit, offset=offset
        )

    async def create_department(
        self,
        token: str | None,
        organization_id: UUID,
        *,
        name: str,
        csrf: str | None,
    ) -> DepartmentResponse:
        """Создаёт отдел через User Service."""
        actor = await authorize_session(
            self._sessions, token, "users.roles.assign", csrf=csrf or ""
        )
        return await self._organizations.create_department(
            actor.user_id, organization_id, name=name
        )

    async def list_memberships(
        self, token: str | None, user_id: UUID
    ) -> tuple[MembershipResponse, ...]:
        """Читает принадлежность выбранного профиля."""
        actor = await authorize_session(self._sessions, token, "admin.access")
        return await self._organizations.list_memberships(actor.user_id, user_id)

    async def set_membership(
        self,
        token: str | None,
        user_id: UUID,
        organization_id: UUID,
        department_id: UUID,
        *,
        active: bool,
        authorization_version: int,
        csrf: str | None,
    ) -> MembershipChangeResponse:
        """Сравнивает версию прав при назначении или снятии членства."""
        actor = await authorize_session(
            self._sessions, token, "users.roles.assign", csrf=csrf or ""
        )
        return await self._organizations.set_membership(
            actor.user_id,
            user_id,
            organization_id,
            department_id,
            active=active,
            authorization_version=authorization_version,
        )
