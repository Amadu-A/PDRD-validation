# services/user-service/src/pdrd_user_service/transport/http/routers/equipment_sources_access.py

"""Закрытый маршрут назначения права управления источниками оборудования для действующего администратора."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.equipment_sources_access import (
    EquipmentSourcesAccessManagement,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.equipment_sources_access_schemas import (
    ChangeEquipmentSourcesAccessRequest,
    EquipmentSourcesAccessChangeResponse,
)

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["equipment-sources-access-internal"],
    dependencies=[Depends(require_service_key)],
)


def _management(container: Container) -> EquipmentSourcesAccessManagement:
    """Закрывает маршрут, если сценарий не инициализирован."""
    if container.equipment_sources_access is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.equipment_sources_access


Management = Annotated[EquipmentSourcesAccessManagement, Depends(_management)]
ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


@router.patch(
    "/{user_id}/equipment-sources-access",
    response_model=EquipmentSourcesAccessChangeResponse,
)
async def change_equipment_sources_access(
    user_id: UUID,
    command: ChangeEquipmentSourcesAccessRequest,
    actor_user_id: ActorId,
    management: Management,
) -> EquipmentSourcesAccessChangeResponse:
    """Передаёт доверенного актёра в сценарий с проверкой текущих ролей в БД."""
    try:
        result = await management.change(
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            enabled=command.enabled,
            authorization_version=command.authorization_version,
        )
    except AdminRequired as error:
        raise HTTPException(403, "Недостаточно прав.") from error
    except UserNotFound as error:
        raise HTTPException(404, "Профиль не найден.") from error
    except AuthorizationConflict as error:
        raise HTTPException(409, "Версия прав изменилась.") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return EquipmentSourcesAccessChangeResponse.from_domain(result)
