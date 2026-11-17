# services/document-service/src/pdrd_document_service/core/settings.py

"""Runtime-конфигурация Document Service."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal[
    "local",
    "dev",
    "test",
    "stage",
    "prod",
]


class PdfSettings(BaseModel):
    """Ограничения PDF extraction."""

    max_upload_mb: int = Field(
        default=200,
        ge=1,
        le=1000,
    )

    max_annotation_payload_mb: int = Field(default=32, ge=1, le=200)

    render_max_side: int = Field(
        default=2400,
        ge=500,
        le=10000,
    )

    max_analysis_pages: int = Field(
        default=200,
        ge=1,
        le=200,
    )

    max_context_pages: int = Field(
        default=100,
        ge=2,
        le=1000,
    )

    text_limit: int = Field(
        default=12000,
        ge=100,
        le=100000,
    )

    @property
    def max_upload_bytes(self) -> int:
        """Возвращает ограничение upload в байтах."""
        return self.max_upload_mb * 1024 * 1024


class CadSettings(BaseModel):
    """Настройки DWG/DXF processing."""

    max_upload_mb: int = Field(
        default=200,
        ge=1,
        le=1000,
    )

    dwg_converter_command: str = "dwg2dxf"

    dwg_converter_timeout_seconds: int = Field(
        default=180,
        ge=1,
        le=1800,
    )

    render_dpi: int = Field(
        default=180,
        ge=72,
        le=600,
    )

    render_max_side: int = Field(
        default=2600,
        ge=500,
        le=10000,
    )

    machine_text_limit: int = Field(
        default=14000,
        ge=1000,
        le=100000,
    )

    text_sample_limit: int = Field(
        default=160,
        ge=1,
        le=5000,
    )

    block_sample_limit: int = Field(
        default=120,
        ge=1,
        le=5000,
    )

    dangling_sample_limit: int = Field(
        default=120,
        ge=1,
        le=5000,
    )

    connectivity_tolerance: float = Field(
        default=0.5,
        gt=0,
        le=1000,
    )

    virtual_insert_depth: int = Field(
        default=2,
        ge=0,
        le=10,
    )

    @property
    def max_upload_bytes(self) -> int:
        """Возвращает максимальный размер CAD upload."""
        return self.max_upload_mb * 1024 * 1024


class SpecificationServiceSettings(BaseModel):
    """Контракт существующего Spec Extracting AI и выделенный интеграционный ключ."""

    enabled: bool = False
    base_url: str = "http://spec-extracting-ai-api-1:8080"
    api_key: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=30, gt=0, le=180)
    total_timeout_seconds: float = Field(default=180, gt=0, le=900)
    poll_interval_seconds: float = Field(default=0.5, gt=0, le=10)
    cache_version: str = "spec-api-v1-9844c4e"
    artifacts_path: Path = Path("/data/extraction-artifacts/specification")
    allow_native_fallback: bool = True

    @model_validator(mode="after")
    def require_key(self):
        """Не позволяет молча запускать включённую интеграцию без выделенного ключа."""
        if self.enabled and not self.api_key.get_secret_value():
            raise ValueError(
                "Для Spec Extracting AI требуется выделенный интеграционный ключ."
            )
        return self


class DoclingSettings(BaseModel):
    """Выборочный CPU Docling Serve из общего инфраструктурного контракта."""

    enabled: bool = False
    base_url: str = "http://docling-eval:5001"
    api_key: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=30, gt=0, le=180)
    total_timeout_seconds: float = Field(default=90, gt=0, le=300)
    poll_interval_seconds: float = Field(default=0.5, gt=0, le=10)
    concurrency: int = Field(default=2, ge=1, le=4)
    ocr_preset: str = "tesseract"
    ocr_languages: tuple[str, ...] = ("rus", "eng")
    cache_version: str = "docling-serve-1.38.2-v1"
    artifacts_path: Path = Path("/data/extraction-artifacts/docling")

    @model_validator(mode="after")
    def require_key(self):
        """Не включает внешний OCR без настроенного ключа доступа."""
        if self.enabled and not self.api_key.get_secret_value():
            raise ValueError("Для Docling требуется выделенный ключ доступа.")
        return self


class Settings(BaseSettings):
    """Настройки процесса Document Service."""

    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_prefix="DOCUMENT_SERVICE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    service_name: str = "PDRD Document Service"
    service_version: str = "0.1.0"

    environment: EnvironmentName = "local"

    host: str = "0.0.0.0"

    port: int = Field(
        default=8301,
        ge=1,
        le=65535,
    )

    docs_enabled: bool = True

    spec_extracting_ai_base_url: str | None = Field(
        default=None, validation_alias="SPEC_EXTRACTING_AI_BASE_URL", exclude=True
    )
    spec_extracting_ai_api_key: SecretStr | None = Field(
        default=None, validation_alias="SPEC_EXTRACTING_AI_API_KEY", exclude=True
    )
    docling_base_url: str | None = Field(
        default=None, validation_alias="DOCLING_BASE_URL", exclude=True
    )
    docling_api_key: SecretStr | None = Field(
        default=None, validation_alias="DOCLING_API_KEY", exclude=True
    )

    @model_validator(mode="before")
    @classmethod
    def shared_integration_aliases(cls, values):
        """Принимает публичные имена интеграций из .env до проверки вложенных настроек."""
        values = dict(values)
        for field, mappings in (
            (
                "specification_service",
                (
                    ("SPEC_EXTRACTING_AI_BASE_URL", "base_url"),
                    ("SPEC_EXTRACTING_AI_API_KEY", "api_key"),
                ),
            ),
            (
                "docling",
                (("DOCLING_BASE_URL", "base_url"), ("DOCLING_API_KEY", "api_key")),
            ),
        ):
            for alias, target in mappings:
                if values.get(alias) is not None:
                    current = values.get(field, {})
                    nested = (
                        current.model_dump()
                        if isinstance(current, BaseModel)
                        else dict(current)
                    )
                    nested[target] = values[alias]
                    values[field] = nested
        return values

    pdf: PdfSettings = Field(
        default_factory=PdfSettings,
    )
    specification_service: SpecificationServiceSettings = Field(
        default_factory=SpecificationServiceSettings
    )
    docling: DoclingSettings = Field(default_factory=DoclingSettings)

    cad: CadSettings = Field(
        default_factory=CadSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached runtime settings."""
    return Settings()
