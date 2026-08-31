# services/api-gateway/src/pdrd_api_gateway/core/settings.py

"""Конфигурация микросервиса API Gateway.

Модуль является единой типизированной точкой чтения переменных окружения
сервиса. Application и transport-код не должны обращаться к os.getenv()
напрямую.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal[
    "local",
    "dev",
    "test",
    "stage",
    "prod",
]


class Settings(BaseSettings):
    """Описывает runtime-конфигурацию API Gateway.

    Значения сначала берутся из committed baseline `.env.example`,
    затем могут быть переопределены `.env` и переменными процесса.
    Префикс изолирует настройки Gateway от остальных микросервисов.
    """

    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_prefix="API_GATEWAY_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "PDRD API Gateway"
    service_version: str = "0.1.0"
    environment: EnvironmentName = "local"

    host: str = "0.0.0.0"
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
    )

    docs_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    """Возвращает единственный экземпляр конфигурации процесса.

    Returns:
        Провалидированная конфигурация API Gateway.
    """
    return Settings()
