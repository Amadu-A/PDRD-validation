# services/user-service/src/pdrd_user_service/transport/http/routers/public_profiles.py

"""Закрытый пакетный справочник авторов для Gateway Experience."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from pdrd_user_service.application.use_cases.public_profiles import ProfileReadDenied
from pdrd_user_service.domain.access import Role
from pdrd_user_service.transport.http.dependencies import Container, require_service_key

router = APIRouter(
    prefix="/internal/v1/users", dependencies=[Depends(require_service_key)]
)


class PublicProfilesRequest(BaseModel):
    """Ограничивает размер пакетного запроса и запрещает браузерные роли/актёра."""

    model_config = ConfigDict(extra="forbid")
    user_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class PublicProfileResponse(BaseModel):
    """Отдаёт только необходимые таблице Experience поля профиля."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)
    user_id: UUID
    login: str | None
    display_name: str
    roles: tuple[Role, ...]


class PublicProfilesResponse(BaseModel):
    """Возвращает существующие профили в ограниченном пакете."""

    model_config = ConfigDict(extra="forbid")
    items: tuple[PublicProfileResponse, ...]


@router.post("/public-profiles", response_model=PublicProfilesResponse)
async def read_public_profiles(
    request: PublicProfilesRequest,
    container: Container,
    actor_user_id: Annotated[UUID, Header(alias="X-PDRD-Actor-Id")],
) -> PublicProfilesResponse:
    """Проверяет сервисный ключ и повторно сверяет право человека в User Service."""
    if container.public_profiles is None:
        raise HTTPException(503, "Справочник авторов недоступен")
    try:
        result = await container.public_profiles.read(
            actor_user_id=actor_user_id, user_ids=request.user_ids
        )
    except ProfileReadDenied as error:
        raise HTTPException(403, "Недостаточно прав") from error
    return PublicProfilesResponse(
        items=tuple(PublicProfileResponse.model_validate(item) for item in result)
    )
