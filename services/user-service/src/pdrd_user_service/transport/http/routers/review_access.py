# services/user-service/src/pdrd_user_service/transport/http/routers/review_access.py

"""Закрытый маршрут изменения ревью для действующего администратора."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.review_access import ReviewAccessManagement
from pdrd_user_service.application.use_cases.users import AdminRequired, UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.review_access_schemas import (
    ChangeReviewAccessRequest,
    ReviewAccessChangeResponse,
)

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["review-access-internal"],
    dependencies=[Depends(require_service_key)],
)


def _management(container: Container) -> ReviewAccessManagement:
    """Закрывает маршрут, если сценарий не инициализирован."""
    if container.review_access is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.review_access


Management = Annotated[ReviewAccessManagement, Depends(_management)]
ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


@router.patch("/{user_id}/review-access", response_model=ReviewAccessChangeResponse)
async def change_review_access(
    user_id: UUID,
    command: ChangeReviewAccessRequest,
    actor_user_id: ActorId,
    management: Management,
) -> ReviewAccessChangeResponse:
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
    return ReviewAccessChangeResponse.from_domain(result)
