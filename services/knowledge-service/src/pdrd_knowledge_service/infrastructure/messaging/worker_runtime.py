# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/worker_runtime.py

"""Runtime composition одного задания нормативной индексации."""

from functools import partial
from uuid import UUID

from pdrd_knowledge_service.application.use_cases.index_normative_document import (
    IndexNormativeDocument,
)
from pdrd_knowledge_service.core.settings import (
    get_settings,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    NormativeDocument,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.embedding.ollama import (
    OllamaEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.pdf.pymupdf import (
    PyMuPdfNormativePdfExtractor,
)
from pdrd_knowledge_service.infrastructure.storage.filesystem import (
    LocalFilesystemNormativeDocumentStorage,
)
from pdrd_knowledge_service.infrastructure.vector_store.qdrant import (
    QdrantVectorStore,
)


async def execute_normative_indexing(
    *,
    document_id: UUID,
) -> NormativeDocument:
    """Собирает adapters и индексирует один managed document."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    unit_of_work_factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork,
        session_factory,
    )

    storage = LocalFilesystemNormativeDocumentStorage(
        root_path=settings.storage.root_path,
    )

    embedding_provider = OllamaEmbeddingProvider(
        base_url=settings.embedding.base_url,
        model=settings.embedding.model,
        request_timeout_seconds=(settings.embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.embedding.health_timeout_seconds),
    )

    vector_store = QdrantVectorStore(
        base_url=settings.qdrant.base_url,
        request_timeout_seconds=(settings.qdrant.request_timeout_seconds),
        health_timeout_seconds=(settings.qdrant.health_timeout_seconds),
    )

    use_case = IndexNormativeDocument(
        unit_of_work_factory=unit_of_work_factory,
        storage=storage,
        pdf_extractor=PyMuPdfNormativePdfExtractor(),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        collection=settings.qdrant.normative_collection,
        chunk_size=settings.indexing.chunk_size,
        chunk_overlap=settings.indexing.chunk_overlap,
        embed_batch_size=settings.indexing.embed_batch_size,
        upsert_batch_size=settings.indexing.upsert_batch_size,
    )

    try:
        return await use_case.execute(
            document_id=document_id,
        )

    finally:
        await engine.dispose()
