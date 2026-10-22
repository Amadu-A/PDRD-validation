# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/worker_runtime.py

"""Собирает зависимости задания индексации нормативки и личных пакетов."""

from functools import partial
from uuid import UUID

from pdrd_knowledge_service.application.use_cases.index_normative_document import (
    IndexNormativeDocument,
)
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
)
from pdrd_knowledge_service.core.embedding_runtime import build_embedding_runtime
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
from pdrd_knowledge_service.infrastructure.database.section_locks import (
    PostgresCatalogSectionLocks,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.embedding.text_http import (
    HttpTextEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.office.libreoffice import (
    LibreOfficeNormativeOfficeToPdfConverter,
)
from pdrd_knowledge_service.infrastructure.pdf.pymupdf import (
    PyMuPdfNormativePdfExtractor,
)
from pdrd_knowledge_service.infrastructure.pdf.technical_assignment import (
    PyMuPdfTechnicalAssignmentProcessor,
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
    """Индексирует документ нормативки или личного пакета по общей модели."""
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

    embedding_provider = HttpTextEmbeddingProvider(
        runtime=build_embedding_runtime(
            settings, engine, stage="normative_indexing", document_id=str(document_id)
        ),
        base_url=settings.embedding.base_url,
        request_timeout_seconds=(settings.embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.embedding.health_timeout_seconds),
    )

    use_case = IndexNormativeDocument(
        atomic_requirements=NormativeRequirementCatalog(
            unit_of_work_factory, policy=settings.normative_requirements.policy()
        ),
        unit_of_work_factory=unit_of_work_factory,
        section_locks=PostgresCatalogSectionLocks(engine),
        storage=LocalFilesystemNormativeDocumentStorage(
            root_path=settings.storage.root_path,
        ),
        pdf_extractor=PyMuPdfNormativePdfExtractor(),
        embedding_provider=embedding_provider,
        vector_store=QdrantVectorStore(
            base_url=settings.qdrant.base_url,
            request_timeout_seconds=(settings.qdrant.request_timeout_seconds),
            health_timeout_seconds=(settings.qdrant.health_timeout_seconds),
        ),
        collection=settings.qdrant.normative_collection,
        chunk_size=settings.indexing.chunk_size,
        chunk_overlap=settings.indexing.chunk_overlap,
        embed_batch_size=(settings.indexing.embed_batch_size),
        upsert_batch_size=(settings.indexing.upsert_batch_size),
        office_converter=(
            LibreOfficeNormativeOfficeToPdfConverter(
                executable=(settings.office_conversion.executable),
                timeout_seconds=(settings.office_conversion.timeout_seconds),
            )
        ),
    )

    try:
        return await use_case.execute(
            document_id=document_id,
        )

    finally:
        await engine.dispose()


async def execute_normative_requirements_indexing(
    *, document_id: UUID, generation_id: str
) -> dict:
    """Запускает отдельную задачу того же worker без embedding и векторных мутаций."""
    settings = get_settings()
    engine = build_async_engine(settings.database)
    factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork, build_session_factory(engine)
    )
    try:
        return await BuildNormativeRequirements(
            unit_of_work_factory=factory,
            storage=LocalFilesystemNormativeDocumentStorage(
                root_path=settings.storage.root_path
            ),
            pdf_extractor=PyMuPdfNormativePdfExtractor(
                ocr_processor=PyMuPdfTechnicalAssignmentProcessor(
                    ocr_executable=settings.technical_assignment.ocr_executable,
                    ocr_language=settings.technical_assignment.ocr_language,
                    ocr_timeout_seconds=min(
                        settings.technical_assignment.ocr_timeout_seconds,
                        settings.normative_requirements.stale_seconds
                        / (2 * max(1, settings.normative_requirements.max_ocr_pages)),
                    ),
                ),
                max_ocr_pages=settings.normative_requirements.max_ocr_pages,
            ),
            section_locks=PostgresCatalogSectionLocks(engine),
            policy=settings.normative_requirements.policy(),
            office_converter=LibreOfficeNormativeOfficeToPdfConverter(
                executable=settings.office_conversion.executable,
                timeout_seconds=settings.office_conversion.timeout_seconds,
            ),
        ).execute(document_id=document_id, generation_id=generation_id)
    finally:
        await engine.dispose()
