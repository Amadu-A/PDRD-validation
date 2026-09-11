# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/technical_assignment_worker_runtime.py

"""Runtime composition T-indexing worker."""

from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import UUID

from pdrd_knowledge_service.application.use_cases.index_technical_assignment import (
    IndexTechnicalAssignment,
)
from pdrd_knowledge_service.core.settings import (
    get_settings,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.technical_assignment_persistence import (
    SqlAlchemyTechnicalAssignmentUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.embedding.multimodal_http import (
    HttpMultimodalEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.office.libreoffice import (
    LibreOfficeNormativeOfficeToPdfConverter,
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


async def execute_technical_assignment_indexing(
    *,
    technical_assignment_id: UUID,
    allow_retry: bool,
) -> TechnicalAssignment:
    """Индексирует ТЗ через unified embedding model."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    unit_of_work_factory = partial(
        SqlAlchemyTechnicalAssignmentUnitOfWork,
        session_factory,
    )

    multimodal = HttpMultimodalEmbeddingProvider(
        base_url=(settings.embedding.base_url),
        request_timeout_seconds=(settings.embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.embedding.health_timeout_seconds),
    )

    technical_assignment_settings = settings.technical_assignment

    use_case = IndexTechnicalAssignment(
        unit_of_work_factory=unit_of_work_factory,
        storage=(
            LocalFilesystemNormativeDocumentStorage(
                root_path=Path(
                    technical_assignment_settings.storage_root_path,
                ),
            )
        ),
        pdf_processor=(
            PyMuPdfTechnicalAssignmentProcessor(
                ocr_min_text_chars=(technical_assignment_settings.ocr_min_text_chars),
                ocr_executable=(technical_assignment_settings.ocr_executable),
                ocr_language=(technical_assignment_settings.ocr_language),
                ocr_page_segmentation_mode=(
                    technical_assignment_settings.ocr_page_segmentation_mode
                ),
                ocr_timeout_seconds=(technical_assignment_settings.ocr_timeout_seconds),
            )
        ),
        embedding_provider=multimodal,
        vector_store=QdrantVectorStore(
            base_url=(settings.qdrant.base_url),
            request_timeout_seconds=(settings.qdrant.request_timeout_seconds),
            health_timeout_seconds=(settings.qdrant.health_timeout_seconds),
        ),
        office_converter=(
            LibreOfficeNormativeOfficeToPdfConverter(
                executable=(settings.office_conversion.executable),
                timeout_seconds=(settings.office_conversion.timeout_seconds),
            )
        ),
        collection=(settings.qdrant.multimodal_collection),
        output_dimension=(settings.embedding_dimension),
        max_pages=(technical_assignment_settings.max_pages),
        render_dpi=(technical_assignment_settings.render_dpi),
        max_image_pixels=(settings.multimodal_embedding.max_image_pixels),
        page_text_limit=(technical_assignment_settings.page_text_limit),
    )

    try:
        return await use_case.execute(
            technical_assignment_id=(technical_assignment_id),
            allow_retry=allow_retry,
        )

    finally:
        await engine.dispose()


async def touch_technical_assignment_indexing(
    *,
    technical_assignment_id: UUID,
) -> None:
    """Обновляет heartbeat INDEXING ТЗ короткой transaction."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    try:
        async with SqlAlchemyTechnicalAssignmentUnitOfWork(
            session_factory,
        ) as unit_of_work:
            assignment = await unit_of_work.assignments.get_for_update(
                technical_assignment_id,
            )

            if (
                assignment is None
                or assignment.index_status
                is not TechnicalAssignmentIndexStatus.INDEXING
            ):
                return

            changed = assignment.touch_indexing(
                changed_at=datetime.now(
                    UTC,
                ),
            )

            await unit_of_work.assignments.update(
                changed,
            )

            await unit_of_work.commit()

    finally:
        await engine.dispose()
