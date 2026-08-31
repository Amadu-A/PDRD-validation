# services/api-gateway/src/pdrd_api_gateway/core/settings.py

"""Конфигурация микросервиса API Gateway."""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal[
    "local",
    "dev",
    "test",
    "stage",
    "prod",
]


class DatabaseSettings(BaseModel):
    """Настройки project-specific PostgreSQL API Gateway."""

    host: str = "postgres"

    port: int = Field(
        default=5432,
        ge=1,
        le=65535,
    )

    name: str = "pdrd"
    user: str = "pdrd"

    password: SecretStr = SecretStr(
        "change-me",
    )

    pool_size: int = Field(
        default=5,
        ge=1,
        le=50,
    )

    max_overflow: int = Field(
        default=10,
        ge=0,
        le=100,
    )

    pool_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )

    connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )

    health_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30,
    )


class BrokerSettings(BaseModel):
    """Настройки project-specific RabbitMQ namespace."""

    host: str = "rabbitmq"

    port: int = Field(
        default=5672,
        ge=1,
        le=65535,
    )

    user: str = "pdrd_validation"

    password: SecretStr = SecretStr(
        "change-me",
    )

    virtual_host: str = "pdrd-validation"

    queue_name: str = "pdrd.analysis"
    exchange_name: str = "pdrd.analysis"
    routing_key: str = "analysis.execute"

    connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )

    health_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30,
    )

    result_expires_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
    )


class Settings(BaseSettings):
    """Описывает runtime-конфигурацию API Gateway."""

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

    database: DatabaseSettings = Field(
        default_factory=DatabaseSettings,
    )

    broker: BrokerSettings = Field(
        default_factory=BrokerSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает единственный экземпляр конфигурации процесса."""
    return Settings()
