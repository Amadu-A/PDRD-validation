# services/user-service/src/pdrd_user_service/transport/http/review_access_schemas.py

"""Строгие команды и ответы назначения ревью без полей аутентификации."""

from dataclasses import asdict

from pydantic import Field, StrictBool, StrictInt

from pdrd_user_service.application.use_cases.review_access import ReviewAccessChange
from pdrd_user_service.transport.http.schemas import StrictSchema, UserResponse


class ChangeReviewAccessRequest(StrictSchema):
    """Принимает только булев флаг и целую ожидаемую версию."""

    enabled: StrictBool
    authorization_version: StrictInt = Field(ge=1)


class ReviewAccessChangeResponse(StrictSchema):
    """Возвращает сохранённую версию и действующие права выбранного профиля."""

    user: UserResponse
    review_access: bool
    review_access_automatic: bool
    review_access_editable: bool

    @classmethod
    def from_domain(cls, result: ReviewAccessChange) -> "ReviewAccessChangeResponse":
        """Сериализует только разрешённый профиль и вычисленное состояние."""
        return cls(user=UserResponse.from_domain(result.user), **asdict(result.state))
