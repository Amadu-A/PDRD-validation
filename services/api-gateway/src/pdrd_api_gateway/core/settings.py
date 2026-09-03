# services/api-gateway/src/pdrd_api_gateway/core/settings.py

"""Конфигурация микросервиса API Gateway."""

from functools import lru_cache
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

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


class OutboxSettings(BaseModel):
    """Настройки transactional outbox dispatcher."""

    poll_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        le=60,
    )

    batch_size: int = Field(
        default=20,
        ge=1,
        le=500,
    )


class StorageSettings(BaseModel):
    """Настройки временного хранения документов."""

    root_path: str = "/data/analyses"

    max_upload_mb: int = Field(
        default=200,
        ge=1,
        le=1000,
    )

    @property
    def max_upload_bytes(
        self,
    ) -> int:
        """Возвращает максимальный размер файла."""
        return self.max_upload_mb * 1024 * 1024


class OrchestrationSettings(BaseModel):
    """Настройки опубликованных PDRD n8n workflows."""

    base_url: str = "http://n8n:5678"

    pdf_webhook_path: str = "/webhook/analysis/v2/pdf"

    cad_webhook_path: str = "/webhook/analysis/v2/cad"

    pdf_cad_webhook_path: str = "/webhook/analysis/v2/pdf-cad"

    request_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        le=7200,
    )

    connect_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=120,
    )


class KnowledgeServiceSettings(BaseModel):
    """Настройки internal API Knowledge Service."""

    base_url: str = "http://pdrd-knowledge-service:8401"

    request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
    )

    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )


class ProjectContextCleanupSettings(BaseModel):
    """Настройки страховочного cleanup через Knowledge Service."""

    base_url: str = "http://pdrd-knowledge-service:8401"

    request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
    )

    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
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

    outbox: OutboxSettings = Field(
        default_factory=OutboxSettings,
    )

    storage: StorageSettings = Field(
        default_factory=StorageSettings,
    )

    orchestration: OrchestrationSettings = Field(
        default_factory=OrchestrationSettings,
    )

    knowledge_service: KnowledgeServiceSettings = Field(
        default_factory=KnowledgeServiceSettings,
    )

    project_context_cleanup: ProjectContextCleanupSettings = Field(
        default_factory=ProjectContextCleanupSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает единственный экземпляр конфигурации."""
    return Settings()
