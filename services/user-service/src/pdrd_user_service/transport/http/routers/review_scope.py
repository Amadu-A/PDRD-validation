# services/user-service/src/pdrd_user_service/transport/http/routers/review_scope.py

"""Закрытый маршрут проверки доступа руководителя к Review проектировщика."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from pdrd_user_service.application.use_cases.review_scope import ReviewScopeAccess
from pdrd_user_service.transport.http.dependencies import Container, require_service_key
from pdrd_user_service.transport.http.review_scope_schemas import (
    ReviewScopeRequest,
    ReviewScopeResponse,
)

router = APIRouter(
    prefix="/internal/v1/access",
    tags=["review-scope-internal"],
    dependencies=[Depends(require_service_key)],
)


def _scope_access(container: Container) -> ReviewScopeAccess:
    """Запрещает проверку через неинициализированный каталог."""
    if container.review_scope_access is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Каталог недоступен.")
    return container.review_scope_access


ScopeAccess = Annotated[ReviewScopeAccess, Depends(_scope_access)]


@router.post("/review-scope", response_model=ReviewScopeResponse)
async def review_scope(
    command: ReviewScopeRequest, scope_access: ScopeAccess
) -> ReviewScopeResponse:
    """Возвращает только boolean после проверки текущей роли и членства."""
    allowed = await scope_access.can_read(**command.model_dump())
    return ReviewScopeResponse(allowed=allowed)
