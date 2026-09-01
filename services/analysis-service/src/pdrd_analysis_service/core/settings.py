# services/analysis-service/src/pdrd_analysis_service/core/settings.py

"""Pydantic Settings Analysis Service."""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
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


class VisionSettings(BaseModel):
    """Настройки shared Ollama VLM."""

    base_url: str = "http://ollama:11434"

    model: str = "qwen3-vl:8b-instruct"

    request_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        le=7200,
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

    num_ctx: int = Field(
        default=16384,
        ge=1024,
        le=131072,
    )

    max_retries: int = Field(
        default=2,
        ge=1,
        le=5,
    )

    keep_alive: str = "1m"

    max_retry_num_predict: int = Field(
        default=6000,
        ge=1,
        le=20000,
    )


class PipelineSettings(BaseModel):
    """Настройки VLM pipeline."""

    page_facts_num_predict: int = Field(
        default=1600,
        ge=1,
        le=10000,
    )

    norm_check_num_predict: int = Field(
        default=2600,
        ge=1,
        le=10000,
    )

    final_num_predict: int = Field(
        default=1800,
        ge=1,
        le=10000,
    )

    max_issues: int = Field(
        default=10,
        ge=1,
        le=50,
    )

    final_batch_size: int = Field(
        default=2,
        ge=1,
        le=10,
    )

    normative_text_limit: int = Field(
        default=700,
        ge=100,
        le=5000,
    )

    experience_context_limit: int = Field(
        default=600,
        ge=100,
        le=5000,
    )

    experience_min_score: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
    )

    max_normative_queries: int = Field(
        default=7,
        ge=1,
        le=20,
    )

    max_image_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )


class ProjectContextSettings(BaseModel):
    """Настройки анализа диапазона ПЗ."""

    classify_batch_size: int = Field(
        default=8,
        ge=1,
        le=50,
    )

    classify_num_predict: int = Field(
        default=1200,
        ge=100,
        le=10000,
    )

    min_text_length: int = Field(
        default=80,
        ge=1,
        le=10000,
    )

    reject_confidence: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
    )

    context_text_limit: int = Field(
        default=900,
        ge=100,
        le=5000,
    )

    query_source_text_limit: int = Field(
        default=1500,
        ge=100,
        le=10000,
    )


class Settings(BaseSettings):
    """Runtime settings Analysis Service."""

    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_prefix="ANALYSIS_SERVICE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "PDRD Analysis Service"

    service_version: str = "0.1.0"

    environment: EnvironmentName = "local"

    host: str = "0.0.0.0"

    port: int = Field(
        default=8501,
        ge=1,
        le=65535,
    )

    docs_enabled: bool = True

    vision: VisionSettings = Field(
        default_factory=VisionSettings,
    )

    pipeline: PipelineSettings = Field(
        default_factory=PipelineSettings,
    )

    project_context: ProjectContextSettings = Field(
        default_factory=(ProjectContextSettings),
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached settings."""
    return Settings()
