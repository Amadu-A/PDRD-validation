# services/knowledge-service/src/pdrd_knowledge_service/core/settings.py

"""Runtime-конфигурация Knowledge Service."""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal[
    "local",
    "dev",
    "test",
    "stage",
    "prod",
]


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

    embedding: EmbeddingSettings = Field(
        default_factory=EmbeddingSettings,
    )

    qdrant: QdrantSettings = Field(
        default_factory=QdrantSettings,
    )

    search: SearchSettings = Field(
        default_factory=SearchSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached runtime settings."""
    return Settings()
