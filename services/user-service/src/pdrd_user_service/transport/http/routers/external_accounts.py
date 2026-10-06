# services/user-service/src/pdrd_user_service/transport/http/routers/external_accounts.py

"""Внутренние маршруты жизненного цикла профиля с email-входом."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    IdentityConflict,
)
from pdrd_user_service.application.ports.section_catalog import (
    SectionCatalogUnavailable,
)
from pdrd_user_service.application.use_cases.external_accounts import (
    ExternalAccountConflict,
    ExternalAccounts,
)
from pdrd_user_service.application.use_cases.users import UserNotFound
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.external_schemas import (
    ExternalRegistrationRequest,
    VerifyEmailRequest,
)
from pdrd_user_service.transport.http.schemas import UserResponse

router = APIRouter(
    prefix="/internal/v1/users",
    tags=["external-accounts-internal"],
    dependencies=[Depends(require_service_key)],
)


def _external_accounts(container: Container) -> ExternalAccounts:
    """Не допускает регистрацию без запущенного каталога."""
    if container.external_accounts is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.external_accounts


Accounts = Annotated[ExternalAccounts, Depends(_external_accounts)]


@router.post("/external", response_model=UserResponse)
async def register_external(
    command: ExternalRegistrationRequest, accounts: Accounts
) -> UserResponse:
    """Сохраняет ожидающий профиль после создания учётных данных Auth Service."""
    try:
        user = await accounts.register(**command.model_dump())
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    except (IdentityConflict, ExternalAccountConflict) as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Конфликт регистрации."
        ) from error
    return UserResponse.from_domain(user)


@router.post("/{user_id}/verify-email", response_model=UserResponse)
async def verify_external_email(
    user_id: UUID, command: VerifyEmailRequest, accounts: Accounts
) -> UserResponse:
    """Активирует только профиль, связанный с проверенной учётной записью."""
    try:
        user = await accounts.verify_email(user_id=user_id, subject=command.subject)
    except SectionCatalogUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог разделов недоступен."
        ) from error
    except UserNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Профиль не найден.") from error
    except (IdentityConflict, ExternalAccountConflict, AuthorizationConflict) as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Профиль не активирован."
        ) from error
    return UserResponse.from_domain(user)
