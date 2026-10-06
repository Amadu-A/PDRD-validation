# services/admin-service/src/pdrd_admin_service/transport/http/routers/organizations.py

"""Браузерные маршруты управления организациями, отделами и членством."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Header, Query

from pdrd_admin_service.contracts.organization_models import (
    DepartmentPage,
    MembershipChangeResponse,
    MembershipResponse,
    MembershipVersionRequest,
    NameRequest,
    OrganizationPage,
    OrganizationResponse,
)
from pdrd_admin_service.transport.http.dependencies import Organizations

router = APIRouter(prefix="/api/v1/admin", tags=["admin-organizations"])
SessionCookie = Annotated[str | None, Cookie(alias="pdrd_session")]
CsrfHeader = Annotated[str | None, Header(alias="X-CSRF-Token")]


@router.get("/organizations", response_model=OrganizationPage)
async def list_organizations(
    organizations: Organizations,
    pdrd_session: SessionCookie = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrganizationPage:
    """Показывает страницу организаций только администратору."""
    return await organizations.list_organizations(
        pdrd_session, limit=limit, offset=offset
    )


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    command: NameRequest,
    organizations: Organizations,
    pdrd_session: SessionCookie = None,
    csrf_token: CsrfHeader = None,
) -> OrganizationResponse:
    """Создаёт организацию после сверки сессии и CSRF."""
    return await organizations.create_organization(
        pdrd_session, name=command.name, csrf=csrf_token
    )


@router.get(
    "/organizations/{organization_id}/departments", response_model=DepartmentPage
)
async def list_departments(
    organization_id: UUID,
    organizations: Organizations,
    pdrd_session: SessionCookie = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DepartmentPage:
    """Показывает страницу отделов выбранной организации."""
    return await organizations.list_departments(
        pdrd_session, organization_id, limit=limit, offset=offset
    )


@router.get(
    "/users/{user_id}/memberships", response_model=tuple[MembershipResponse, ...]
)
async def list_memberships(
    user_id: UUID,
    organizations: Organizations,
    pdrd_session: SessionCookie = None,
) -> tuple[MembershipResponse, ...]:
    """Показывает членства выбранного пользователя."""
    return await organizations.list_memberships(pdrd_session, user_id)


async def _set_membership(
    user_id: UUID,
    organization_id: UUID,
    department_id: UUID,
    command: MembershipVersionRequest,
    organizations: Organizations,
    pdrd_session: str | None,
    csrf_token: str | None,
    *,
    active: bool,
) -> MembershipChangeResponse:
    """Передаёт только проверенную команду в отдельный административный use case."""
    return await organizations.set_membership(
        pdrd_session,
        user_id,
        organization_id,
        department_id,
        active=active,
        authorization_version=command.authorization_version,
        csrf=csrf_token,
    )


@router.put(
    "/users/{user_id}/memberships/{organization_id}/{department_id}",
    response_model=MembershipChangeResponse,
)
async def activate_membership(
    user_id: UUID,
    organization_id: UUID,
    department_id: UUID,
    command: MembershipVersionRequest,
    organizations: Organizations,
    pdrd_session: SessionCookie = None,
    csrf_token: CsrfHeader = None,
) -> MembershipChangeResponse:
    """Назначает пользователю членство в отделе."""
    return await _set_membership(
        user_id,
        organization_id,
        department_id,
        command,
        organizations,
        pdrd_session,
        csrf_token,
        active=True,
    )


@router.delete(
    "/users/{user_id}/memberships/{organization_id}/{department_id}",
    response_model=MembershipChangeResponse,
)
async def deactivate_membership(
    user_id: UUID,
    organization_id: UUID,
    department_id: UUID,
    command: MembershipVersionRequest,
    organizations: Organizations,
    pdrd_session: SessionCookie = None,
    csrf_token: CsrfHeader = None,
) -> MembershipChangeResponse:
    """Снимает членство и повышает версию полномочий."""
    return await _set_membership(
        user_id,
        organization_id,
        department_id,
        command,
        organizations,
        pdrd_session,
        csrf_token,
        active=False,
    )
