# services/user-service/src/pdrd_user_service/transport/http/routers/local_accounts.py

"""Закрытый контракт выбора источника входа и серверного bootstrap профиля."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from pdrd_user_service.application.ports.repository import (
    BootstrapAlreadyPerformed,
    IdentityConflict,
)
from pdrd_user_service.application.use_cases.local_superuser import LocalSuperusers
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.routers.users import Directory
from pdrd_user_service.transport.http.schemas import StrictSchema, UserResponse

router = APIRouter(
    prefix="/internal/v1/users", dependencies=[Depends(require_service_key)]
)


class LoginLookupRequest(StrictSchema):
    """Логин передаётся в теле, чтобы не попасть в URL журналов proxy."""

    login: str = Field(min_length=1, max_length=320)


class LocalSuperuserRequest(StrictSchema):
    """Принимает лишь устойчивый subject и имя; пароля в каталоге нет."""

    subject: UUID
    username: str = Field(min_length=1, max_length=64)


def _superusers(container: Container) -> LocalSuperusers:
    """Требует работающий каталог локального bootstrap."""
    if container.local_superusers is None:
        raise HTTPException(503, "Каталог недоступен.")
    return container.local_superusers


Superusers = Annotated[LocalSuperusers, Depends(_superusers)]


@router.post("/lookup-login", response_model=UserResponse)
async def lookup_login(
    command: LoginLookupRequest, directory: Directory
) -> UserResponse:
    """Возвращает сохранённый источник входа только доверенному Auth Service."""
    try:
        user = await directory.find_by_login(command.login)
    except IdentityConflict as error:
        raise HTTPException(409, "Неоднозначный логин.") from error
    if user is None:
        raise HTTPException(404, "Пользователь не найден.")
    return UserResponse.from_domain(user)


@router.post("/local-superuser", response_model=UserResponse)
async def local_superuser(
    command: LocalSuperuserRequest, superusers: Superusers
) -> UserResponse:
    """Завершает серверную команду; Gateway не публикует этот маршрут."""
    try:
        user = await superusers.create(**command.model_dump())
    except (BootstrapAlreadyPerformed, IdentityConflict) as error:
        raise HTTPException(409, "Первый администратор уже создан.") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return UserResponse.from_domain(user)
