# services/knowledge-service/src/pdrd_knowledge_service/core/settings.py

"""Runtime-конфигурация Knowledge Service."""

from functools import lru_cache
from pathlib import Path
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

from pdrd_knowledge_service.domain.embedding_index import (
    EmbeddingIdentity,
    EmbeddingIndexPlan,
)

EnvironmentName = Literal[
    "local",
    "dev",
    "test",
    "stage",
    "prod",
]


class DatabaseSettings(BaseModel):
    """Настройки PostgreSQL."""

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
    """Настройки RabbitMQ."""

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


class TechnicalAssignmentQueueSettings(BaseModel):
    """Отдельная RabbitMQ очередь ТЗ."""

    queue_name: str = "pdrd.knowledge.technical-assignment"

    exchange_name: str = "pdrd.knowledge.technical-assignment"

    routing_key: str = "technical_assignment.index"

    prefetch_count: int = Field(
        default=1,
        ge=1,
        le=10,
    )


class OutboxSettings(BaseModel):
    """Настройки transactional outbox."""

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
    """Managed storage N/U документов."""

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
        """Возвращает upload limit."""
        return self.max_upload_mb * 1024 * 1024


class NormativeIndexingSettings(BaseModel):
    """Параметры managed catalog indexing."""

    chunk_size: int = Field(
        default=3500,
        ge=100,
        le=20000,
    )

    chunk_overlap: int = Field(
        default=500,
        ge=0,
        le=10000,
    )

    embed_batch_size: int = Field(
        default=64,
        ge=1,
        le=1000,
    )

    upsert_batch_size: int = Field(
        default=64,
        ge=1,
        le=1000,
    )


class TechnicalAssignmentSettings(BaseModel):
    """Параметры T ingestion/indexing."""

    storage_root_path: Path = Path(
        "/data/technical-assignments",
    )

    max_upload_mb: int = Field(
        default=100,
        ge=1,
        le=500,
    )

    max_pages: int = Field(
        default=300,
        ge=1,
        le=2000,
    )

    render_dpi: int = Field(
        default=150,
        ge=72,
        le=300,
    )

    page_text_limit: int = Field(
        default=16_000,
        ge=1000,
        le=100_000,
    )

    retrieval_top_k: int = Field(
        default=6,
        ge=1,
        le=50,
    )

    max_sources: int = Field(
        default=12,
        ge=1,
        le=100,
    )

    retry_delay_seconds: int = Field(
        default=30,
        ge=1,
        le=3600,
    )

    max_retries: int = Field(
        default=8,
        ge=0,
        le=100,
    )

    @property
    def max_upload_bytes(
        self,
    ) -> int:
        """Возвращает upload limit."""
        return self.max_upload_mb * 1024 * 1024


class OfficeConversionSettings(BaseModel):
    """Нормализация Word через LibreOffice."""

    executable: str = "soffice"

    timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=900,
    )


class EmbeddingSettings(BaseModel):
    """Unified embedding-service transport."""

    base_url: str = "http://pdrd-multimodal-embedding-service:8601"

    model: str = "Qwen/Qwen3-VL-Embedding-8B"

    output_dimension: int = Field(
        default=4096,
        ge=64,
        le=4096,
    )

    request_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        le=7200,
    )

    connect_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
    )

    health_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )


class MultimodalEmbeddingSettings(EmbeddingSettings):
    """Backward-compatible T settings той же unified model."""

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


class QdrantSettings(BaseModel):
    """Qdrant aliases и physical collection prefixes."""

    base_url: str = "http://qdrant:6333"

    normative_collection: str = "dva_catalog_active"

    experience_collection: str = "dva_experience_active"

    multimodal_collection: str = "dva_technical_assignment_active"

    catalog_collection_prefix: str = "dva_catalog"

    experience_collection_prefix: str = "dva_experience"

    technical_assignment_collection_prefix: str = "dva_technical_assignment"

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

    legacy_collections: tuple[str, ...] = (
        "dva_normative_v2",
        "dva_experience_v2",
        "dva_multimodal_qwen3vl2b_v1",
        "dva_multimodal_qwen3vl8b_v1",
        "dva_multimodal_v1",
    )


class SearchSettings(BaseModel):
    """Runtime RAG retrieval."""

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
    """Временный PZ Project Context."""

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
        default=64,
        ge=1,
        le=1000,
    )

    upsert_batch_size: int = Field(
        default=64,
        ge=1,
        le=1000,
    )

    collection_prefix: str = "pdrd_project_context"


class Settings(BaseSettings):
    """Настройки Knowledge Service."""

    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_prefix="KNOWLEDGE_SERVICE_",
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

    embedding_schema_version: int = Field(
        default=1,
        ge=1,
        le=1000,
        validation_alias="PDRD_EMBEDDING_SCHEMA_VERSION",
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

    technical_assignment_queue: TechnicalAssignmentQueueSettings = Field(
        default_factory=(TechnicalAssignmentQueueSettings),
    )

    outbox: OutboxSettings = Field(
        default_factory=OutboxSettings,
    )

    storage: NormativeStorageSettings = Field(
        default_factory=NormativeStorageSettings,
    )

    indexing: NormativeIndexingSettings = Field(
        default_factory=NormativeIndexingSettings,
    )

    technical_assignment: TechnicalAssignmentSettings = Field(
        default_factory=TechnicalAssignmentSettings,
    )

    office_conversion: OfficeConversionSettings = Field(
        default_factory=OfficeConversionSettings,
    )

    embedding: EmbeddingSettings = Field(
        default_factory=EmbeddingSettings,
    )

    multimodal_embedding: MultimodalEmbeddingSettings = Field(
        default_factory=MultimodalEmbeddingSettings,
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

    @model_validator(
        mode="after",
    )
    def synchronize_embedding_identity(
        self,
    ) -> "Settings":
        """Запрещает divergence text/T embedding models."""
        identity_update = {
            "model": self.embedding_model,
            "output_dimension": (self.embedding_dimension),
        }

        self.embedding = self.embedding.model_copy(
            update=identity_update,
        )

        self.multimodal_embedding = self.multimodal_embedding.model_copy(
            update=identity_update,
        )

        return self

    @property
    def embedding_identity(
        self,
    ) -> EmbeddingIdentity:
        """Возвращает immutable vector-space identity."""
        return EmbeddingIdentity(
            model=self.embedding_model,
            dimension=self.embedding_dimension,
            schema_version=(self.embedding_schema_version),
        )

    @property
    def embedding_index_plan(
        self,
    ) -> EmbeddingIndexPlan:
        """Возвращает alias/physical collection plan."""
        return EmbeddingIndexPlan(
            identity=self.embedding_identity,
            catalog_alias=(self.qdrant.normative_collection),
            technical_assignment_alias=(self.qdrant.multimodal_collection),
            experience_alias=(self.qdrant.experience_collection),
            catalog_prefix=(self.qdrant.catalog_collection_prefix),
            technical_assignment_prefix=(
                self.qdrant.technical_assignment_collection_prefix
            ),
            experience_prefix=(self.qdrant.experience_collection_prefix),
        )


@lru_cache
def get_settings() -> Settings:
    """Возвращает cached runtime settings."""
    return Settings()
