# services/user-service/src/pdrd_user_service/transport/http/routers/section_access.py

"""Закрытый API назначенных разделов для Gateway и Admin Service."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key

router = APIRouter(
    prefix="/internal/v1/users", dependencies=[Depends(require_service_key)]
)


class SectionAccessResponse(BaseModel):
    """Возвращает только UUID разделов и текущую версию прав."""

    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    section_ids: tuple[UUID, ...]
    all_sections: bool
    authorization_version: int


@router.get("/{user_id}/section-access", response_model=SectionAccessResponse)
async def read_section_access(
    user_id: UUID,
    container: Container,
    actor_user_id: Annotated[UUID, Header(alias="X-PDRD-Actor-Id")],
) -> SectionAccessResponse:
    """Передаёт проверку собственного или административного доступа в прикладной слой."""
    if container.user_sections is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен")
    try:
        result = await container.user_sections.read(
            actor_user_id=actor_user_id, target_user_id=user_id
        )
    except AdminRequired as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав") from error
    except UserNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Профиль не найден") from error
    return SectionAccessResponse(
        user_id=result.user_id,
        section_ids=result.section_ids,
        all_sections=result.all_sections,
        authorization_version=result.authorization_version,
    )
