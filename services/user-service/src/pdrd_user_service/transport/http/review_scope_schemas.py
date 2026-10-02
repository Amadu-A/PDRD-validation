# services/user-service/src/pdrd_user_service/transport/http/review_scope_schemas.py

"""Минимальный внутренний контракт проверки области чужого Review."""

from uuid import UUID

from pdrd_user_service.transport.http.schemas import StrictSchema


class ReviewScopeRequest(StrictSchema):
    """UUID актёра из сессии Gateway и владельца задания."""

    actor_user_id: UUID
    owner_user_id: UUID


class ReviewScopeResponse(StrictSchema):
    """Ответ без причин отказа и персональных данных."""

    allowed: bool
