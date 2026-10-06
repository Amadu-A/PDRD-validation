# services/user-service/src/pdrd_user_service/transport/http/routers/admin_users.py

"""Защищённое перечисление профилей для отдельного Admin Service."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from pdrd_user_service.application.use_cases.list_users import AdminUserListing
from pdrd_user_service.application.use_cases.users import AdminRequired
from pdrd_user_service.transport.http.admin_user_schemas import UserPageResponse
from pdrd_user_service.transport.http.dependencies import Container, require_service_key

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["admin-users-internal"],
    dependencies=[Depends(require_service_key)],
)


def _listing(container: Container) -> AdminUserListing:
    """Запрещает выдавать список при выключенном каталоге."""
    if container.user_listing is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.user_listing


Listing = Annotated[AdminUserListing, Depends(_listing)]
ActorId = Annotated[UUID, Header(alias="X-PDRD-Actor-Id")]


@router.get("", response_model=UserPageResponse)
async def list_users(
    actor_user_id: ActorId,
    listing: Listing,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPageResponse:
    """Возвращает страницу после проверки сервисного ключа и роли в БД."""
    try:
        page = await listing.page(
            actor_user_id=actor_user_id, limit=limit, offset=offset
        )
    except AdminRequired as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав.") from error
    return UserPageResponse.from_domain(page)
