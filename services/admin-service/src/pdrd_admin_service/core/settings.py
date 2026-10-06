# services/admin-service/src/pdrd_admin_service/core/settings.py

"""Загружает конфигурацию admin-service из общих env.example и env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Отключает маршруты до настройки внутренних ключей и адресов сервисов."""

    model_config = SettingsConfigDict(
        env_file=(".env.example", ".env"),
        env_file_encoding="utf-8",
        env_prefix="ADMIN_SERVICE_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "dev", "test", "stage", "prod"] = "local"
    enabled: bool = False
    auth_service_url: str = "http://auth-service:8000"
    auth_service_internal_key: SecretStr = SecretStr("")
    knowledge_service_url: str = "http://knowledge-service:8401"
    user_service_url: str = "http://user-service:8000"
    user_service_internal_key: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @model_validator(mode="after")
    def require_runtime_secrets(self) -> "Settings":
        """Не поднимает рабочие маршруты с пустыми служебными секретами."""
        if self.enabled:
            for name in ("auth_service_internal_key", "user_service_internal_key"):
                if len(getattr(self, name).get_secret_value()) < 32:
                    raise ValueError(f"ADMIN_SERVICE_{name.upper()} слишком короткий")
            for name in (
                "auth_service_url",
                "user_service_url",
                "knowledge_service_url",
            ):
                if not getattr(self, name).startswith(("http://", "https://")):
                    raise ValueError(
                        f"ADMIN_SERVICE_{name.upper()} должен быть HTTP URL"
                    )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает один снимок настроек на процесс."""
    return Settings()
