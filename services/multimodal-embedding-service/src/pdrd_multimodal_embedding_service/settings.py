# services/multimodal-embedding-service/src/pdrd_multimodal_embedding_service/settings.py

"""Runtime settings multimodal embedding service."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
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

TorchDtype = Literal[
    "bfloat16",
    "float16",
]


class ModelSettings(BaseModel):
    """Настройки unified Qwen3-VL-Embedding runtime."""

    name: str = "Qwen/Qwen3-VL-Embedding-8B"

    output_dimension: int = Field(
        default=4096,
        ge=64,
        le=4096,
    )

    model_context_tokens: int = Field(
        default=32768,
        ge=8192,
        le=32768,
    )

    max_input_tokens: int = Field(
        default=8192,
        ge=512,
        le=32768,
    )

    max_image_pixels: int = Field(
        default=1_843_200,
        ge=4096,
        le=16_777_216,
    )

    max_image_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )

    max_batch_size: int = Field(
        default=1,
        ge=1,
        le=8,
    )

    max_concurrency: int = Field(
        default=1,
        ge=1,
        le=8,
    )

    min_free_ram_gib: float = Field(
        default=20.0,
        ge=1.0,
        le=1024.0,
    )

    min_free_vram_gib: float = Field(
        default=18.0,
        ge=1.0,
        le=128.0,
    )

    idle_release_seconds: float = Field(
        default=60.0,
        ge=0.01,
        le=3600.0,
    )

    admission_wait_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        le=7200,
    )

    admission_poll_seconds: float = Field(
        default=1.0,
        gt=0,
        le=30,
    )

    gpu_lease_path: Path = Path(
        "/var/lock/pdrd-gpu/gpu.lock",
    )

    gpu_lease_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        le=7200,
    )

    gpu_lease_poll_seconds: float = Field(
        default=0.25,
        gt=0,
        le=30,
    )

    dtype: TorchDtype = "bfloat16"

    @property
    def min_free_ram_bytes(
        self,
    ) -> int:
        """Возвращает RAM admission threshold."""
        return int(self.min_free_ram_gib * 1024**3)

    @property
    def min_free_vram_bytes(
        self,
    ) -> int:
        """Возвращает VRAM admission threshold."""
        return int(self.min_free_vram_gib * 1024**3)


class Settings(BaseSettings):
    """Настройки процесса embedding service."""

    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_prefix="MULTIMODAL_EMBEDDING_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    embedding_model: str = Field(
        default="Qwen/Qwen3-VL-Embedding-8B",
        validation_alias="PDRD_EMBEDDING_MODEL",
    )

    embedding_dimension: int = Field(
        default=4096,
        ge=64,
        le=4096,
        validation_alias="PDRD_EMBEDDING_DIMENSION",
    )

    service_name: str = "PDRD Multimodal Embedding Service"

    service_version: str = "0.1.0"

    environment: EnvironmentName = "local"

    host: str = "0.0.0.0"

    port: int = Field(
        default=8601,
        ge=1,
        le=65535,
    )

    docs_enabled: bool = True

    model: ModelSettings = Field(
        default_factory=ModelSettings,
    )

    @model_validator(
        mode="after",
    )
    def apply_shared_embedding_identity(
        self,
    ) -> "Settings":
        """Синхронизирует runtime с PDRD embedding identity."""
        self.model = self.model.model_copy(
            update={
                "name": self.embedding_model,
                "output_dimension": (self.embedding_dimension),
            }
        )

        return self


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached runtime settings."""
    return Settings()
