# services/knowledge-service/src/pdrd_knowledge_service/core/container.py

"""Composition root Knowledge Service."""

from dataclasses import dataclass

from pdrd_knowledge_service.application.use_cases.experience import (
    SearchExperience,
)
from pdrd_knowledge_service.application.use_cases.health import (
    CheckReadiness,
)
from pdrd_knowledge_service.application.use_cases.normative import (
    SearchNormative,
)
from pdrd_knowledge_service.application.use_cases.project_context import (
    CreateProjectContext,
    DeleteProjectContext,
    SearchProjectContext,
)
from pdrd_knowledge_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_knowledge_service.infrastructure.embedding.ollama import (
    OllamaEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.vector_store.qdrant import (
    QdrantVectorStore,
)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит runtime dependencies Knowledge Service."""

    settings: Settings

    search_normative: SearchNormative
    search_experience: SearchExperience
    check_readiness: CheckReadiness

    create_project_context: CreateProjectContext | None = None

    search_project_context: SearchProjectContext | None = None

    delete_project_context: DeleteProjectContext | None = None


def build_container() -> ApplicationContainer:
    """Собирает concrete dependencies сервиса."""
    settings = get_settings()

    embedding_provider = OllamaEmbeddingProvider(
        base_url=(settings.embedding.base_url),
        model=(settings.embedding.model),
        request_timeout_seconds=(settings.embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.embedding.health_timeout_seconds),
    )

    vector_store = QdrantVectorStore(
        base_url=(settings.qdrant.base_url),
        request_timeout_seconds=(settings.qdrant.request_timeout_seconds),
        health_timeout_seconds=(settings.qdrant.health_timeout_seconds),
    )

    search_normative = SearchNormative(
        embedding_provider=(embedding_provider),
        vector_store=vector_store,
        collection=(settings.qdrant.normative_collection),
        embedding_model=(settings.embedding.model),
        top_k=(settings.search.normative_top_k),
        max_sources=(settings.search.normative_max_sources),
    )

    search_experience = SearchExperience(
        embedding_provider=(embedding_provider),
        vector_store=vector_store,
        collection=(settings.qdrant.experience_collection),
        embedding_model=(settings.embedding.model),
        top_k=(settings.search.experience_top_k),
    )

    check_readiness = CheckReadiness(
        embedding_provider=(embedding_provider),
        vector_store=vector_store,
        normative_collection=(settings.qdrant.normative_collection),
        experience_collection=(settings.qdrant.experience_collection),
    )

    project_settings = settings.project_context

    return ApplicationContainer(
        settings=settings,
        search_normative=search_normative,
        search_experience=search_experience,
        check_readiness=check_readiness,
        create_project_context=(
            CreateProjectContext(
                embedding_provider=(embedding_provider),
                vector_store=(vector_store),
                collection_prefix=(project_settings.collection_prefix),
                embedding_model=(settings.embedding.model),
                chunk_size=(project_settings.chunk_size),
                chunk_overlap=(project_settings.chunk_overlap),
                embed_batch_size=(project_settings.embed_batch_size),
                upsert_batch_size=(project_settings.upsert_batch_size),
            )
        ),
        search_project_context=(
            SearchProjectContext(
                embedding_provider=(embedding_provider),
                vector_store=(vector_store),
                collection_prefix=(project_settings.collection_prefix),
                embedding_model=(settings.embedding.model),
                top_k=(project_settings.top_k),
            )
        ),
        delete_project_context=(
            DeleteProjectContext(
                vector_store=(vector_store),
                collection_prefix=(project_settings.collection_prefix),
            )
        ),
    )
