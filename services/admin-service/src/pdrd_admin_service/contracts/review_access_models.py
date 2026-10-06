# services/admin-service/src/pdrd_admin_service/contracts/review_access_models.py

"""Строгие контракты назначения ревью через административный фасад."""

from pydantic import Field, StrictBool, StrictInt

from pdrd_admin_service.contracts.models import StrictModel, UserResponse


class ChangeReviewAccessRequest(StrictModel):
    """Содержит только новое значение галочки и ожидаемую версию прав."""

    enabled: StrictBool
    authorization_version: StrictInt = Field(ge=1)


class ReviewAccessChangeResponse(StrictModel):
    """Возвращает подтверждённые User Service профиль и состояние ревью."""

    user: UserResponse
    review_access: StrictBool
    review_access_automatic: StrictBool
    review_access_editable: StrictBool
