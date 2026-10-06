# services/user-service/src/pdrd_user_service/transport/http/routers/normative_access.py

"""Закрытый маршрут назначения права удаления нормативных объектов для действующего администратора."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.normative_access import (
    NormativeAccessManagement,
)
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.normative_access_schemas import (
    ChangeNormativeAccessRequest,
    NormativeAccessChangeResponse,
)

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["normative-access-internal"],
    dependencies=[Depends(require_service_key)],
)


def _management(container: Container) -> NormativeAccessManagement:
    """Закрывает маршрут, если сценарий не инициализирован."""
    if container.normative_access is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.normative_access


Management = Annotated[NormativeAccessManagement, Depends(_management)]
ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


@router.patch(
    "/{user_id}/normative-access", response_model=NormativeAccessChangeResponse
)
async def change_normative_access(
    user_id: UUID,
    command: ChangeNormativeAccessRequest,
    actor_user_id: ActorId,
    management: Management,
) -> NormativeAccessChangeResponse:
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
    return NormativeAccessChangeResponse.from_domain(result)
