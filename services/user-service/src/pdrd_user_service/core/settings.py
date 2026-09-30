# services/user-service/src/pdrd_user_service/core/settings.py

"""Изолированная конфигурация User Service с безопасным запуском по умолчанию.

Проектный каталог настроек читается из .env.example, затем приватные
переопределения из .env и переменные окружения процесса.
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    """Параметры только собственной схемы users в PostgreSQL проекта."""

    host: str = Field(default="postgres", min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = Field(default="pdrd", min_length=1)
    user: str = Field(default="pdrd", min_length=1)
    password: SecretStr = SecretStr("change-me")
    pool_size: int = Field(default=5, ge=1, le=50)
    max_overflow: int = Field(default=10, ge=0, le=100)
    pool_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    health_timeout_seconds: float = Field(default=5.0, gt=0, le=60)


class Settings(BaseSettings):
    """Хранит параметры запуска, внутренний ключ и подключение к своей БД."""

    model_config = SettingsConfigDict(
        env_file=(".env.example", ".env"),
        env_file_encoding="utf-8",
        env_prefix="USER_SERVICE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "dev", "test", "stage", "prod"] = "local"
    enabled: bool = False
    internal_key: SecretStr = SecretStr("")
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    @model_validator(mode="after")
    def require_runtime_secrets(self) -> "Settings":
        """Не запускает рабочий API без ключа или настоящего пароля БД."""
        if self.enabled:
            if len(self.internal_key.get_secret_value()) < 32:
                raise ValueError(
                    "USER_SERVICE_INTERNAL_KEY должен содержать не менее 32 символов"
                )
            if self.database.password.get_secret_value() in {"", "change-me"}:
                raise ValueError("Пароль PostgreSQL User Service не задан")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Создаёт единственный снимок конфигурации процесса."""
    return Settings()
