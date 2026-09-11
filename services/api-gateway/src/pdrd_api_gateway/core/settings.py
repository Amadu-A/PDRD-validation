# services/api-gateway/src/pdrd_api_gateway/core/settings.py

"""Конфигурация микросервиса API Gateway."""

from functools import lru_cache
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    model_validator,
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
    """Настройки project RabbitMQ namespace."""

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

    message_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
    )

    queue_expires_seconds: int = Field(
        default=86400,
        ge=3600,
        le=2_592_000,
    )

    task_expires_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
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

    result_expires_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
    )


class OutboxSettings(BaseModel):
    """Transactional outbox dispatcher."""

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


class AnalysisLifecycleSettings(BaseModel):
    """Bounded lifecycle одного пользовательского анализа."""

    max_runtime_seconds: int = Field(
        default=1740,
        ge=60,
        le=3600,
    )

    task_soft_time_limit_seconds: int = Field(
        default=1770,
        ge=60,
        le=3600,
    )

    task_hard_time_limit_seconds: int = Field(
        default=1800,
        ge=60,
        le=3600,
    )

    transient_max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
    )

    transient_retry_delay_seconds: int = Field(
        default=10,
        ge=1,
        le=300,
    )

    min_retry_budget_seconds: int = Field(
        default=300,
        ge=1,
        le=1800,
    )

    max_attempts: int = Field(
        default=3,
        ge=1,
        le=20,
    )

    heartbeat_interval_seconds: int = Field(
        default=15,
        ge=5,
        le=300,
    )

    stale_processing_seconds: int = Field(
        default=120,
        ge=30,
        le=3600,
    )

    recovery_interval_seconds: int = Field(
        default=30,
        ge=5,
        le=600,
    )

    recovery_batch_size: int = Field(
        default=50,
        ge=1,
        le=1000,
    )

    @model_validator(
        mode="after",
    )
    def validate_deadlines(
        self,
    ) -> "AnalysisLifecycleSettings":
        """Проверяет порядок application/soft/hard deadlines."""
        if not (
            self.max_runtime_seconds
            < self.task_soft_time_limit_seconds
            < self.task_hard_time_limit_seconds
        ):
            raise ValueError(
                "Analysis lifecycle требует max_runtime < soft_limit < hard_limit.",
            )

        if self.stale_processing_seconds <= self.heartbeat_interval_seconds * 2:
            raise ValueError(
                "stale_processing_seconds должен быть больше двух heartbeat interval.",
            )

        if self.min_retry_budget_seconds >= self.max_runtime_seconds:
            raise ValueError(
                "min_retry_budget_seconds должен быть меньше max_runtime_seconds.",
            )

        return self


class StorageSettings(BaseModel):
    """Temporary analysis storage."""

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
        """Возвращает максимальный размер."""
        return self.max_upload_mb * 1024 * 1024


class TechnicalAssignmentSettings(
    BaseModel,
):
    """Gateway lifecycle ТЗ."""

    max_upload_mb: int = Field(
        default=100,
        ge=1,
        le=500,
    )

    index_wait_timeout_seconds: float = Field(
        default=1200.0,
        gt=0,
        le=7200,
    )

    index_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60,
    )

    @property
    def max_upload_bytes(
        self,
    ) -> int:
        """Возвращает T upload limit."""
        return self.max_upload_mb * 1024 * 1024


class OrchestrationSettings(BaseModel):
    """Published PDRD n8n workflows."""

    base_url: str = "http://n8n:5678"

    pdf_webhook_path: str = "/webhook/analysis/v2/pdf"

    cad_webhook_path: str = "/webhook/analysis/v2/cad"

    pdf_cad_webhook_path: str = "/webhook/analysis/v2/pdf-cad"

    request_timeout_seconds: float = Field(
        default=1680.0,
        gt=0,
        le=3600,
    )

    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )


class KnowledgeServiceSettings(BaseModel):
    """Internal API Knowledge Service."""

    base_url: str = "http://pdrd-knowledge-service:8401"

    request_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=600,
    )

    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )

    max_upload_mb: int = Field(
        default=200,
        ge=1,
        le=1000,
    )

    @property
    def max_upload_bytes(
        self,
    ) -> int:
        """Gateway limit managed upload."""
        return self.max_upload_mb * 1024 * 1024


class ProjectContextCleanupSettings(BaseModel):
    """Best-effort Project Context cleanup."""

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


class DocumentServiceSettings(BaseModel):
    """Internal Document Service для PDF-preview."""

    base_url: str = "http://pdrd-document-service:8301"

    request_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=600,
    )

    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )


class AnalysisServiceSettings(BaseModel):
    """Internal Analysis Service для lazy bbox localization."""

    base_url: str = "http://pdrd-analysis-service:8501"

    request_timeout_seconds: float = Field(
        default=600.0,
        gt=0,
        le=1200,
    )

    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )


class Settings(BaseSettings):
    """Runtime API Gateway settings."""

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

    lifecycle: AnalysisLifecycleSettings = Field(
        default_factory=AnalysisLifecycleSettings,
    )

    storage: StorageSettings = Field(
        default_factory=StorageSettings,
    )

    technical_assignment: TechnicalAssignmentSettings = Field(
        default_factory=TechnicalAssignmentSettings,
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

    document_service: DocumentServiceSettings = Field(
        default_factory=DocumentServiceSettings,
    )

    analysis_service: AnalysisServiceSettings = Field(
        default_factory=AnalysisServiceSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached settings."""
    return Settings()
