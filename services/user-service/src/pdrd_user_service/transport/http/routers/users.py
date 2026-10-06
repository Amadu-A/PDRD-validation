# services/user-service/src/pdrd_user_service/transport/http/routers/users.py

"""Закрытые операции каталога пользователей для доверенных сервисов PDRD."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    IdentityConflict,
)
from pdrd_user_service.application.ports.section_catalog import (
    SectionCatalogUnavailable,
)
from pdrd_user_service.application.use_cases.users import (
    AdminRequired,
    UserDirectory,
    UserNotFound,
)
from pdrd_user_service.transport.http.dependencies import (
    Container,
    require_service_key,
)
from pdrd_user_service.transport.http.schemas import (
    AssignRoleRequest,
    PermissionResponse,
    ProvisionRequest,
    ResolveIdentityRequest,
    RoleChangeResponse,
    UserResponse,
)

router = APIRouter(
    prefix="/internal/v1",
    tags=["users-internal"],
    dependencies=[Depends(require_service_key)],
)

ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


def _directory(container: Container) -> UserDirectory:
    """Отклоняет обращения к неинициализированному хранилищу."""
    if container.directory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.directory


Directory = Annotated[UserDirectory, Depends(_directory)]


def _raise_domain_error(error: Exception) -> None:
    """Преобразует ожидаемые ошибки в безопасные HTTP коды."""
    if isinstance(error, SectionCatalogUnavailable):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог разделов недоступен."
        ) from error
    if isinstance(error, UserNotFound):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Пользователь не найден."
        ) from error
    if isinstance(error, AdminRequired):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав.") from error
    if isinstance(error, (AuthorizationConflict, IdentityConflict)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Конфликт изменения.") from error
    if isinstance(error, ValueError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    raise error


@router.post("/identities/resolve", response_model=UserResponse)
async def resolve_identity(
    command: ResolveIdentityRequest, directory: Directory
) -> UserResponse:
    """Находит профиль по устойчивой идентичности без логина/email в URL."""
    user = await directory.find_identity(
        command.provider_id, command.namespace, command.subject
    )
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Идентичность не найдена.")
    return UserResponse.from_domain(user)


@router.post("/users", response_model=UserResponse)
async def provision_user(
    command: ProvisionRequest, directory: Directory
) -> UserResponse:
    """Создаёт профиль только после проверки личности вызывающим auth-service."""
    try:
        user = await directory.provision(**command.model_dump())
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    except SectionCatalogUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог разделов недоступен."
        ) from error
    except IdentityConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Идентичность занята.") from error
    return UserResponse.from_domain(user)


@router.get("/users/{user_id}", response_model=UserResponse)
async def read_user(user_id: UUID, directory: Directory) -> UserResponse:
    """Возвращает профиль по внутреннему UUID."""
    try:
        user = await directory.get_user(user_id)
    except UserNotFound as error:
        _raise_domain_error(error)
        raise AssertionError("Недостижимо") from error
    return UserResponse.from_domain(user)


@router.get("/users/{user_id}/permissions", response_model=PermissionResponse)
async def read_permissions(user_id: UUID, directory: Directory) -> PermissionResponse:
    """Возвращает операционные права с версией, без выдачи доступа к объекту."""
    try:
        snapshot = await directory.permissions(user_id)
    except UserNotFound as error:
        _raise_domain_error(error)
        raise AssertionError("Недостижимо") from error
    return PermissionResponse.from_domain(snapshot)


@router.post("/users/{user_id}/roles", response_model=RoleChangeResponse)
async def assign_user_role(
    user_id: UUID,
    command: AssignRoleRequest,
    actor_user_id: ActorId,
    directory: Directory,
) -> RoleChangeResponse:
    """Назначает локальную роль только после проверки администратора в БД."""
    try:
        scope = command.scope.to_domain()
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    try:
        result = await directory.assign(
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            role=command.role,
            scope=scope,
            expires_at=command.expires_at,
        )
    except (UserNotFound, AdminRequired, AuthorizationConflict, ValueError) as error:
        _raise_domain_error(error)
        raise AssertionError("Недостижимо") from error
    return RoleChangeResponse.from_domain(result)


@router.delete(
    "/users/{user_id}/roles/{assignment_id}", response_model=RoleChangeResponse
)
async def revoke_user_role(
    user_id: UUID,
    assignment_id: UUID,
    actor_user_id: ActorId,
    directory: Directory,
) -> RoleChangeResponse:
    """Отзывает локальную роль с атомарным увеличением версии прав."""
    try:
        result = await directory.revoke(
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            assignment_id=assignment_id,
        )
    except (UserNotFound, AdminRequired, AuthorizationConflict, ValueError) as error:
        _raise_domain_error(error)
        raise AssertionError("Недостижимо") from error
    return RoleChangeResponse.from_domain(result)
