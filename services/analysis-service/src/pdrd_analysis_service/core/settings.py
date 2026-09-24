# services/analysis-service/src/pdrd_analysis_service/core/settings.py

"""Pydantic Settings Analysis Service."""

from functools import lru_cache
from pathlib import Path
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


class VllmCacheSettings(BaseModel):
    """Настройки project-local exact-request VLM cache."""

    enabled: bool = False

    root_path: Path = Path(
        "/data/vlm-cache",
    )

    namespace: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
    )

    ttl_seconds: int = Field(
        default=30 * 24 * 60 * 60,
        ge=60,
        le=365 * 24 * 60 * 60,
    )


class VllmSettings(BaseModel):
    """Настройки stable logical shared-vlm contract."""

    base_url: str = "http://shared-vlm:8000/v1"

    model: str = "shared-vlm"

    request_timeout_seconds: float = Field(
        default=600.0,
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

    max_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
    )

    retry_backoff_seconds: float = Field(
        default=1.0,
        ge=0.0,
        le=30.0,
    )

    max_retry_num_predict: int = Field(
        default=14000,
        ge=1,
        le=32000,
    )

    cache: VllmCacheSettings = Field(
        default_factory=VllmCacheSettings,
    )


class AnalysisProgressSettings(BaseModel):
    """Best-effort durable cancellation probe API."""

    base_url: str = "http://api-gateway:8000"

    request_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30,
    )

    connect_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30,
    )


class PipelineSettings(BaseModel):
    """Настройки VLM pipeline."""

    page_facts_num_predict: int = Field(
        default=1600,
        ge=1,
        le=10000,
    )

    norm_check_num_predict: int = Field(
        default=14000,
        ge=1,
        le=20000,
    )

    technical_assignment_num_predict: int = Field(
        default=4000,
        ge=1,
        le=10000,
    )

    technical_assignment_batch_size: int = Field(
        default=48,
        ge=1,
        le=100,
    )

    technical_assignment_max_requirements_per_page: int = Field(
        default=1000,
        ge=1,
        le=10000,
    )

    technical_assignment_requirement_text_limit: int = Field(
        default=1800,
        ge=100,
        le=10000,
    )

    final_num_predict: int = Field(
        default=1800,
        ge=1,
        le=10000,
    )

    finding_location_num_predict: int = Field(
        default=4000,
        ge=200,
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

    max_stage_pages: int = Field(
        default=50,
        ge=1,
        le=200,
    )

    vlm_stage_concurrency: int = Field(
        default=4,
        ge=1,
        le=32,
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

    vlm: VllmSettings = Field(
        default_factory=VllmSettings,
    )

    progress: AnalysisProgressSettings = Field(
        default_factory=AnalysisProgressSettings,
    )

    pipeline: PipelineSettings = Field(
        default_factory=PipelineSettings,
    )

    project_context: ProjectContextSettings = Field(
        default_factory=ProjectContextSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached settings."""
    return Settings()
