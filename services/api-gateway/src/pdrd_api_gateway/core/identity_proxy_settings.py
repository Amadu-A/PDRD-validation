# services/api-gateway/src/pdrd_api_gateway/core/identity_proxy_settings.py

"""Адреса закрытых сервисов для одноимённого браузерного API Gateway."""

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class IdentityProxySettings(BaseModel):
    """Включает маршруты входа и админки только при явной настройке контура."""

    enabled: bool = False
    authorization_enabled: bool = False
    auth_internal_key: SecretStr = SecretStr("")
    user_service_internal_key: SecretStr = SecretStr("")
    technical_assignment_access_key: SecretStr = SecretStr("")
    trusted_proxy_key: SecretStr = SecretStr("")
    public_origin: str = ""
    auth_base_url: str = "http://auth-service:8000"
    user_service_url: str = "http://user-service:8000"
    admin_base_url: str = "http://admin-service:8000"
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @model_validator(mode="after")
    def require_authorization_secrets(self) -> "IdentityProxySettings":
        """Не допускает открытого Gateway при включённой проверке ролей."""
        if self.enabled:
            if not self.authorization_enabled:
                raise ValueError("Браузерный identity proxy требует проверки прав")
            if len(self.auth_internal_key.get_secret_value()) < 32:
                raise ValueError("Для проверки прав нужен служебный ключ Auth Service")
            if len(self.user_service_internal_key.get_secret_value()) < 32:
                raise ValueError(
                    "Для проверки области нужен служебный ключ User Service"
                )
            if len(self.technical_assignment_access_key.get_secret_value()) < 32:
                raise ValueError("Для временного доступа к ТЗ нужен отдельный ключ")
            if len(self.trusted_proxy_key.get_secret_value()) < 32:
                raise ValueError("Для адреса клиента нужен ключ frontend proxy")
            parsed = urlsplit(self.public_origin)
            try:
                valid_port = parsed.port is None or 1 <= parsed.port <= 65535
            except ValueError:
                valid_port = False
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not valid_port
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or self.public_origin.endswith("/")
            ):
                raise ValueError("Проверка прав требует публичный HTTPS origin")
        elif self.authorization_enabled:
            raise ValueError("Проверка прав требует включённого identity proxy")
        return self

    @field_validator("auth_base_url", "admin_base_url", "user_service_url")
    @classmethod
    def require_origin_only_url(cls, value: str) -> str:
        """Запрещает пути, параметры и встроенные пароли в адресе сервиса."""
        parsed = urlsplit(value)
        try:
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not valid_port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Адрес сервиса должен содержать только HTTP(S) origin")
        return value.rstrip("/")
