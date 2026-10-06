# services/admin-service/src/pdrd_admin_service/transport/http/routers/users.py

"""Публикует каталог и роли только после проверки сессии администратора."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Header, Query

from pdrd_admin_service.contracts.models import (
    ReplaceRoleRequest,
    RoleDetailResponse,
    UserPage,
    UserResponse,
)
from pdrd_admin_service.contracts.normative_access_models import (
    ChangeNormativeAccessRequest,
    NormativeAccessChangeResponse,
)
from pdrd_admin_service.contracts.review_access_models import (
    ChangeReviewAccessRequest,
    ReviewAccessChangeResponse,
)
from pdrd_admin_service.contracts.section_models import (
    CatalogSection,
    SectionAccessResponse,
)
from pdrd_admin_service.transport.http.dependencies import Admin

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])
SessionCookie = Annotated[str | None, Cookie(alias="pdrd_session")]
CsrfHeader = Annotated[str | None, Header(alias="X-CSRF-Token")]


@router.get("/section-catalog", response_model=tuple[CatalogSection, ...])
async def list_sections(
    admin: Admin, pdrd_session: SessionCookie = None
) -> tuple[CatalogSection, ...]:
    """Публикует живой каталог разделов для административных чекбоксов."""
    return await admin.list_sections(pdrd_session)


@router.get("/{user_id}/section-access", response_model=SectionAccessResponse)
async def get_sections(
    user_id: UUID, admin: Admin, pdrd_session: SessionCookie = None
) -> SectionAccessResponse:
    """Читает назначенные разделы выбранного пользователя."""
    return await admin.get_sections(pdrd_session, user_id)


@router.get("", response_model=UserPage)
async def list_users(
    admin: Admin,
    pdrd_session: SessionCookie = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    """Читает ограниченную страницу профилей без прямого доступа к БД."""
    return await admin.list_users(pdrd_session, limit=limit, offset=offset)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID, admin: Admin, pdrd_session: SessionCookie = None
) -> UserResponse:
    """Читает карточку пользователя по UUID после проверки cookie."""
    return await admin.get_user(pdrd_session, user_id)


@router.get("/{user_id}/roles", response_model=RoleDetailResponse)
async def get_roles(
    user_id: UUID, admin: Admin, pdrd_session: SessionCookie = None
) -> RoleDetailResponse:
    """Загружает роли для выбранной карточки, не перегружая общий список."""
    return await admin.get_roles(pdrd_session, user_id)


@router.patch("/{user_id}/role", response_model=RoleDetailResponse)
async def replace_role(
    user_id: UUID,
    command: ReplaceRoleRequest,
    admin: Admin,
    pdrd_session: SessionCookie = None,
    csrf_token: CsrfHeader = None,
) -> RoleDetailResponse:
    """Меняет одну рабочую роль с CAS и защитой от CSRF."""
    return await admin.replace_role(pdrd_session, user_id, command, csrf=csrf_token)


@router.patch("/{user_id}/review-access", response_model=ReviewAccessChangeResponse)
async def change_review_access(
    user_id: UUID,
    command: ChangeReviewAccessRequest,
    admin: Admin,
    pdrd_session: SessionCookie = None,
    csrf_token: CsrfHeader = None,
) -> ReviewAccessChangeResponse:
    """Меняет доступ к ревью после проверки административной сессии и CSRF."""
    return await admin.change_review_access(
        pdrd_session, user_id, command, csrf=csrf_token
    )


@router.patch(
    "/{user_id}/normative-access", response_model=NormativeAccessChangeResponse
)
async def change_normative_access(
    user_id: UUID,
    command: ChangeNormativeAccessRequest,
    admin: Admin,
    pdrd_session: SessionCookie = None,
    csrf_token: CsrfHeader = None,
) -> NormativeAccessChangeResponse:
    """Меняет доступ к удалению нормативных объектов после проверки административной сессии и CSRF."""
    return await admin.change_normative_access(
        pdrd_session, user_id, command, csrf=csrf_token
    )
