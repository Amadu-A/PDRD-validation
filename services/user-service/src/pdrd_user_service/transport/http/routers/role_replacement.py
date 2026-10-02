# services/user-service/src/pdrd_user_service/transport/http/routers/role_replacement.py

"""Закрытые маршруты просмотра и замены рабочей роли администратором."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.replace_role import ReplaceWorkerRole
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.role_replacement_schemas import (
    ReplaceRoleRequest,
    RoleReplacementResponse,
)

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["admin-roles-internal"],
    dependencies=[Depends(require_service_key)],
)


def _replacement(container: Container) -> ReplaceWorkerRole:
    """Отклоняет обращения к неинициализированному сценарию."""
    if container.role_replacement is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.role_replacement


Replacement = Annotated[ReplaceWorkerRole, Depends(_replacement)]
ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


def _raise_expected(error: Exception) -> None:
    """Преобразует известный отказ без выдачи внутреннего состояния БД."""
    if isinstance(error, AdminRequired):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав.") from error
    if isinstance(error, UserNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Профиль не найден.") from error
    if isinstance(error, AuthorizationConflict):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Версия прав изменилась."
        ) from error
    if isinstance(error, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    raise error


@router.get("/{user_id}/roles", response_model=RoleReplacementResponse)
async def inspect_user_roles(
    user_id: UUID, actor_user_id: ActorId, replacement: Replacement
) -> RoleReplacementResponse:
    """Показывает действующие роли выбранного профиля администратору."""
    try:
        result = await replacement.inspect(
            actor_user_id=actor_user_id, target_user_id=user_id
        )
    except (AdminRequired, UserNotFound) as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return RoleReplacementResponse.from_domain(result)


@router.patch("/{user_id}/role", response_model=RoleReplacementResponse)
async def replace_user_role(
    user_id: UUID,
    command: ReplaceRoleRequest,
    actor_user_id: ActorId,
    replacement: Replacement,
) -> RoleReplacementResponse:
    """Одним CAS-переходом заменяет или снимает рабочую роль."""
    try:
        scope = command.scope.to_domain() if command.scope is not None else None
        result = await replacement.replace(
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            role=command.role,
            scope=scope,
            authorization_version=command.authorization_version,
        )
    except (AdminRequired, UserNotFound, AuthorizationConflict, ValueError) as error:
        _raise_expected(error)
        raise AssertionError("Недостижимо") from error
    return RoleReplacementResponse.from_domain(result)
