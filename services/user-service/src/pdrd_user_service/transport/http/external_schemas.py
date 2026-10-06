# services/user-service/src/pdrd_user_service/transport/http/external_schemas.py

"""Закрытые входные данные для регистрации внешнего профиля.

Пароль и токен подтверждения намеренно отсутствуют: их принимает Auth Service.
"""

from uuid import UUID

from pydantic import Field

from pdrd_user_service.transport.http.schemas import StrictSchema


class ExternalRegistrationRequest(StrictSchema):
    """Получает устойчивый ключ учётной записи, имя и адрес."""

    subject: UUID
    display_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)


class VerifyEmailRequest(StrictSchema):
    """Связывает подтверждённый токен с исходной учётной записью."""

    subject: UUID
