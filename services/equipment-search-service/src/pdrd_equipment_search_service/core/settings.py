# services/equipment-search-service/src/pdrd_equipment_search_service/core/settings.py

"""Типизированная конфигурация поискового сервиса с sparse .env override."""

from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class DatabaseSettings(BaseModel):
    """Подключение к проектному PostgreSQL и собственной схеме equipment_search."""

    host: str = "postgres"
    port: int = 5432
    name: str = "pdrd"
    user: str = "pdrd"
    password: SecretStr = SecretStr("change-me")

    def url(self) -> str:
        """Кодирует специальные символы пароля без ручной интерполяции."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        ).render_as_string(hide_password=False)


class Settings(BaseSettings):
    """Сначала читает catalog defaults, затем приватные переопределения."""

    model_config = SettingsConfigDict(
        env_prefix="EQUIPMENT_SEARCH_SERVICE_",
        env_nested_delimiter="__",
        env_file=(".env.example", ".env"),
        extra="ignore",
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    searxng_url: str = "http://searxng:8080"
    document_service_url: str = "http://document-service:8301"
    knowledge_service_url: str = "http://knowledge-service:8401"
    storage_root: Path = Path("/data/equipment-documents")
    internal_key: SecretStr = SecretStr("")
    max_download_bytes: int = Field(default=12_000_000, gt=0)
    max_queries: int = Field(default=6, ge=1, le=20)
    max_downloads: int = Field(default=3, ge=1, le=20)
    max_urls_per_query: int = Field(default=8, ge=1, le=30)
    max_models: int = Field(default=12, ge=1, le=100)
    max_vision_calls: int = Field(default=2, ge=0, le=12)
    analysis_service_url: str = "http://analysis-service:8501"
    max_seconds: float = Field(default=180.0, gt=0, le=600)
    max_concurrent_searches: int = Field(default=2, ge=1, le=20)
