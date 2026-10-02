# services/user-service/src/pdrd_user_service/transport/http/routers/organization_memberships.py

"""Закрытые маршруты справочников и членства, принадлежащих User Service."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.organization_memberships import (
    DepartmentNotFound,
    MembershipNotFound,
    OrganizationMemberships,
    OrganizationNotFound,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.organization_schemas import (
    DepartmentPageResponse,
    DepartmentResponse,
    MembershipChangeResponse,
    MembershipResponse,
    MembershipVersionRequest,
    NameRequest,
    OrganizationPageResponse,
    OrganizationResponse,
)

router = APIRouter(
    prefix="/internal/v1",
    tags=["admin-organizations-internal"],
    dependencies=[Depends(require_service_key)],
)
ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


def _operations(container: Container) -> OrganizationMemberships:
    """Отклоняет запрос до настройки хранилища профилей."""
    if container.organization_memberships is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.organization_memberships


Operations = Annotated[OrganizationMemberships, Depends(_operations)]


def _raise_expected(error: Exception) -> None:
    """Скрывает внутренние детали БД и отличает конфликт версии от ошибки формы."""
    if isinstance(error, AdminRequired):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав.") from error
    if isinstance(
        error,
        (UserNotFound, OrganizationNotFound, DepartmentNotFound, MembershipNotFound),
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено.") from error
    if isinstance(error, AuthorizationConflict):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Версия прав изменилась."
        ) from error
    if isinstance(error, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    raise error


@router.get("/organizations", response_model=OrganizationPageResponse)
async def list_organizations(
    actor_user_id: ActorId,
    operations: Operations,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrganizationPageResponse:
    """Возвращает страницу организаций проверенному администратору."""
    try:
        page = await operations.organizations(
            actor_user_id=actor_user_id, limit=limit, offset=offset
        )
    except AdminRequired as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return OrganizationPageResponse.from_domain(page)


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    command: NameRequest, actor_user_id: ActorId, operations: Operations
) -> OrganizationResponse:
    """Создаёт организацию только от имени действующего администратора."""
    try:
        result = await operations.create_organization(
            actor_user_id=actor_user_id, name=command.name
        )
    except (AdminRequired, ValueError) as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return OrganizationResponse.from_domain(result)


@router.get(
    "/organizations/{organization_id}/departments",
    response_model=DepartmentPageResponse,
)
async def list_departments(
    organization_id: UUID,
    actor_user_id: ActorId,
    operations: Operations,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DepartmentPageResponse:
    """Показывает отделы одной организации."""
    try:
        page = await operations.departments(
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            limit=limit,
            offset=offset,
        )
    except (AdminRequired, OrganizationNotFound) as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return DepartmentPageResponse.from_domain(page)


@router.post(
    "/organizations/{organization_id}/departments",
    response_model=DepartmentResponse,
    status_code=201,
)
async def create_department(
    organization_id: UUID,
    command: NameRequest,
    actor_user_id: ActorId,
    operations: Operations,
) -> DepartmentResponse:
    """Создаёт отдел при существующей активной организации."""
    try:
        result = await operations.create_department(
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            name=command.name,
        )
    except (AdminRequired, OrganizationNotFound, ValueError) as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return DepartmentResponse.from_domain(result)


@router.get(
    "/users/{user_id}/memberships", response_model=tuple[MembershipResponse, ...]
)
async def list_memberships(
    user_id: UUID, actor_user_id: ActorId, operations: Operations
) -> tuple[MembershipResponse, ...]:
    """Показывает текущую организационную принадлежность выбранного профиля."""
    try:
        memberships = await operations.memberships(
            actor_user_id=actor_user_id, target_user_id=user_id
        )
    except (AdminRequired, UserNotFound) as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return tuple(MembershipResponse.from_domain(item) for item in memberships)


async def _change_membership(
    user_id: UUID,
    organization_id: UUID,
    department_id: UUID,
    command: MembershipVersionRequest,
    actor_user_id: UUID,
    operations: OrganizationMemberships,
    *,
    active: bool,
) -> MembershipChangeResponse:
    """Выполняет один CAS переход и переводит ожидаемые отказы в HTTP."""
    try:
        result = await operations.set_membership(
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            organization_id=organization_id,
            department_id=department_id,
            active=active,
            authorization_version=command.authorization_version,
        )
    except (
        AdminRequired,
        UserNotFound,
        DepartmentNotFound,
        MembershipNotFound,
        AuthorizationConflict,
        ValueError,
    ) as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return MembershipChangeResponse.from_domain(result)


@router.put(
    "/users/{user_id}/memberships/{organization_id}/{department_id}",
    response_model=MembershipChangeResponse,
)
async def activate_membership(
    user_id: UUID,
    organization_id: UUID,
    department_id: UUID,
    command: MembershipVersionRequest,
    actor_user_id: ActorId,
    operations: Operations,
) -> MembershipChangeResponse:
    """Назначает членство, необходимое для роли руководителя отдела."""
    return await _change_membership(
        user_id,
        organization_id,
        department_id,
        command,
        actor_user_id,
        operations,
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
    actor_user_id: ActorId,
    operations: Operations,
) -> MembershipChangeResponse:
    """Снимает членство и немедленно меняет версию полномочий."""
    return await _change_membership(
        user_id,
        organization_id,
        department_id,
        command,
        actor_user_id,
        operations,
        active=False,
    )
