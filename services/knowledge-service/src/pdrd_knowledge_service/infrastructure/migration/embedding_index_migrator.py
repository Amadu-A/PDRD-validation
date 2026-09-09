# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/migration/embedding_index_migrator.py

"""Startup executable automatic embedding blue/green migration."""

import asyncio
import logging
from functools import partial
from pathlib import Path

from pdrd_knowledge_service.application.use_cases.embedding_migration import (
    MigrateEmbeddingIndexes,
    PersistedEmbeddingCollectionRebuilder,
)
from pdrd_knowledge_service.core.settings import (
    get_settings,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.technical_assignment_persistence import (
    SqlAlchemyTechnicalAssignmentUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
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

LOGGER = logging.getLogger(
    __name__,
)


async def run_migration() -> None:
    """Собирает dependencies и выполняет idempotent migration."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    catalog_uow_factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork,
        session_factory,
    )

    technical_assignment_uow_factory = partial(
        SqlAlchemyTechnicalAssignmentUnitOfWork,
        session_factory,
    )

    vector_store = QdrantVectorStore(
        base_url=settings.qdrant.base_url,
        request_timeout_seconds=(settings.qdrant.request_timeout_seconds),
        health_timeout_seconds=(settings.qdrant.health_timeout_seconds),
    )

    text_embedding = HttpTextEmbeddingProvider(
        base_url=settings.embedding.base_url,
        request_timeout_seconds=(settings.embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.embedding.health_timeout_seconds),
    )

    multimodal_embedding = HttpMultimodalEmbeddingProvider(
        base_url=(settings.multimodal_embedding.base_url),
        request_timeout_seconds=(settings.multimodal_embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.multimodal_embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.multimodal_embedding.health_timeout_seconds),
    )

    office_converter = LibreOfficeNormativeOfficeToPdfConverter(
        executable=(settings.office_conversion.executable),
        timeout_seconds=(settings.office_conversion.timeout_seconds),
    )

    rebuilder = PersistedEmbeddingCollectionRebuilder(
        catalog_uow_factory=catalog_uow_factory,
        technical_assignment_uow_factory=(technical_assignment_uow_factory),
        catalog_storage=(
            LocalFilesystemNormativeDocumentStorage(
                root_path=settings.storage.root_path,
            )
        ),
        technical_assignment_storage=(
            LocalFilesystemNormativeDocumentStorage(
                root_path=Path(
                    settings.technical_assignment.storage_root_path,
                ),
            )
        ),
        pdf_extractor=PyMuPdfNormativePdfExtractor(),
        technical_assignment_pdf_processor=(PyMuPdfTechnicalAssignmentProcessor()),
        office_converter=office_converter,
        text_embedding_provider=text_embedding,
        multimodal_embedding_provider=(multimodal_embedding),
        vector_store=vector_store,
        output_dimension=settings.embedding_dimension,
        chunk_size=settings.indexing.chunk_size,
        chunk_overlap=settings.indexing.chunk_overlap,
        embed_batch_size=(settings.indexing.embed_batch_size),
        upsert_batch_size=(settings.indexing.upsert_batch_size),
        technical_assignment_max_pages=(settings.technical_assignment.max_pages),
        technical_assignment_render_dpi=(settings.technical_assignment.render_dpi),
        technical_assignment_max_image_pixels=(
            settings.multimodal_embedding.max_image_pixels
        ),
        technical_assignment_page_text_limit=(
            settings.technical_assignment.page_text_limit
        ),
    )

    migrator = MigrateEmbeddingIndexes(
        vector_store=vector_store,
        rebuilder=rebuilder,
        plan=settings.embedding_index_plan,
        vector_size=settings.embedding_dimension,
        legacy_collections=(settings.qdrant.legacy_collections),
    )

    try:
        result = await migrator.execute()

        LOGGER.warning(
            (
                "embedding_migration_completed "
                "skipped=%s "
                "catalog_points=%s "
                "technical_assignment_points=%s "
                "experience_points=%s "
                "fingerprint=%s"
            ),
            result.skipped,
            result.catalog_points,
            result.technical_assignment_points,
            result.experience_points,
            settings.embedding_identity.fingerprint,
        )

    finally:
        await engine.dispose()


def main() -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
    )

    asyncio.run(run_migration())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
