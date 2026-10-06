# services/auth-service/src/pdrd_auth_service/transport/http/schemas.py

"""Публичные запросы входа и регистрации с ограниченными полями."""

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    """Отклоняет неожиданные поля, в том числе подмену роли и user_id."""

    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictRequest):
    """Передаёт пароль только сценарию проверки личности."""

    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(StrictRequest):
    """Создаёт внешний профиль, ожидающий подтверждения email."""

    display_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)


class VerifyEmailRequest(StrictRequest):
    """Однократный код из fragment ссылки письма."""

    token: str = Field(min_length=43, max_length=43)


class IntrospectRequest(StrictRequest):
    """Секрет сессии доступен только доверенному внутреннему сервису."""

    token: str = Field(min_length=43, max_length=43)
