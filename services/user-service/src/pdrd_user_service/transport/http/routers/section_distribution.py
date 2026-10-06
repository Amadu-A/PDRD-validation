# services/user-service/src/pdrd_user_service/transport/http/routers/section_distribution.py

"""Внутреннее одноразовое назначение нового раздела после создания в Knowledge."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from pdrd_user_service.application.ports.section_catalog import (
    SectionCatalogUnavailable,
)
from pdrd_user_service.application.use_cases.distribute_section import (
    CatalogSectionNotFound,
    CatalogWriteRequired,
    DistributeSection,
)
from pdrd_user_service.application.use_cases.users import UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key

router = APIRouter(
    prefix="/internal/v1/users/section-catalog",
    tags=["section-distribution-internal"],
    dependencies=[Depends(require_service_key)],
)
ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


class DistributionRequest(BaseModel):
    """Принимает только UUID; роли, владельцы и версии определяются сервером."""

    model_config = ConfigDict(extra="forbid")
    section_id: UUID


class DistributionResponse(BaseModel):
    """Возвращает число выданных назначений и признак повторного вызова."""

    section_id: UUID
    granted_users: int
    already_distributed: bool


def _distribution(container: Container) -> DistributeSection:
    """Отклоняет запросы без запущенного хранилища и каталога."""
    if container.section_distribution is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.section_distribution


Distribution = Annotated[DistributeSection, Depends(_distribution)]


@router.post("/grants", response_model=DistributionResponse)
async def distribute_section(
    command: DistributionRequest, actor_user_id: ActorId, distribution: Distribution
) -> DistributionResponse:
    """От имени проверенного создателя добавляет новый раздел действующим рабочим ролям."""
    try:
        result = await distribution.execute(
            section_id=command.section_id, actor_user_id=actor_user_id
        )
    except (UserNotFound, CatalogWriteRequired) as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав.") from error
    except CatalogSectionNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздел не найден.") from error
    except SectionCatalogUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог разделов недоступен."
        ) from error
    return DistributionResponse(
        section_id=result.section_id,
        granted_users=result.granted_users,
        already_distributed=result.already_distributed,
    )
