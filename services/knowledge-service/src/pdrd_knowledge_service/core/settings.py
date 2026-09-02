# services/knowledge-service/src/pdrd_knowledge_service/core/settings.py

"""Runtime-конфигурация Knowledge Service."""

from functools import lru_cache
from pathlib import Path
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
    """Настройки project-specific PostgreSQL Knowledge Service."""

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
    """Настройки RabbitMQ нормативной индексации."""

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

    queue_name: str = "pdrd.knowledge.indexing"

    exchange_name: str = "pdrd.knowledge.indexing"

    routing_key: str = "normative.index"

    connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )


class OutboxSettings(BaseModel):
    """Настройки Knowledge transactional outbox."""

    poll_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        le=60,
    )

    batch_size: int = Field(
        default=20,
        ge=1,
        le=1000,
    )


class NormativeStorageSettings(BaseModel):
    """Настройки managed storage нормативных PDF."""

    root_path: Path = Path(
        "/data/normative",
    )

    max_upload_mb: int = Field(
        default=200,
        ge=1,
        le=1024,
    )

    @property
    def max_upload_bytes(
        self,
    ) -> int:
        """Возвращает upload limit в bytes."""
        return self.max_upload_mb * 1024 * 1024


class EmbeddingSettings(BaseModel):
    """Настройки embedding provider."""

    base_url: str = "http://ollama:11434"

    model: str = "qwen3-embedding:4b"

    request_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        le=3600,
    )

    connect_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=300,
    )

    health_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )


class QdrantSettings(BaseModel):
    """Настройки Qdrant."""

    base_url: str = "http://qdrant:6333"

    normative_collection: str = "dva_normative_v2"

    experience_collection: str = "dva_experience_v2"

    request_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        le=600,
    )

    health_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )


class SearchSettings(BaseModel):
    """Параметры runtime RAG retrieval."""

    normative_top_k: int = Field(
        default=4,
        ge=1,
        le=100,
    )

    normative_max_sources: int = Field(
        default=12,
        ge=1,
        le=500,
    )

    experience_top_k: int = Field(
        default=3,
        ge=1,
        le=100,
    )


class ProjectContextSettings(BaseModel):
    """Настройки временного Project Context RAG."""

    chunk_size: int = Field(
        default=1800,
        ge=100,
        le=20000,
    )

    chunk_overlap: int = Field(
        default=250,
        ge=0,
        le=10000,
    )

    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
    )

    embed_batch_size: int = Field(
        default=12,
        ge=1,
        le=100,
    )

    upsert_batch_size: int = Field(
        default=64,
        ge=1,
        le=1000,
    )

    collection_prefix: str = "pdrd_project_context"


class Settings(BaseSettings):
    """Настройки процесса Knowledge Service."""

    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_prefix="KNOWLEDGE_SERVICE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "PDRD Knowledge Service"

    service_version: str = "0.1.0"

    environment: EnvironmentName = "local"

    host: str = "0.0.0.0"

    port: int = Field(
        default=8401,
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

    storage: NormativeStorageSettings = Field(
        default_factory=NormativeStorageSettings,
    )

    embedding: EmbeddingSettings = Field(
        default_factory=EmbeddingSettings,
    )

    qdrant: QdrantSettings = Field(
        default_factory=QdrantSettings,
    )

    search: SearchSettings = Field(
        default_factory=SearchSettings,
    )

    project_context: ProjectContextSettings = Field(
        default_factory=ProjectContextSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached runtime settings."""
    return Settings()
