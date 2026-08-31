# services/document-service/src/pdrd_document_service/core/settings.py

"""Runtime-конфигурация Document Service."""

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


class PdfSettings(BaseModel):
    """Ограничения PDF extraction."""

    max_upload_mb: int = Field(
        default=200,
        ge=1,
        le=1000,
    )

    render_max_side: int = Field(
        default=2400,
        ge=500,
        le=10000,
    )

    max_analysis_pages: int = Field(
        default=50,
        ge=1,
        le=500,
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

    pdf: PdfSettings = Field(
        default_factory=PdfSettings,
    )

    cad: CadSettings = Field(
        default_factory=CadSettings,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached runtime settings."""
    return Settings()
