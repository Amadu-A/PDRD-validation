# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/technical_assignment_worker_runtime.py

"""Runtime composition T-indexing worker."""

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
from pdrd_knowledge_service.infrastructure.embedding.ollama_release import (
    OllamaLoadedModelReleaser,
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
    """Собирает T-indexing dependencies."""
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

    storage = LocalFilesystemNormativeDocumentStorage(
        root_path=Path(
            settings.technical_assignment.storage_root_path,
        ),
    )

    multimodal = HttpMultimodalEmbeddingProvider(
        base_url=(settings.multimodal_embedding.base_url),
        request_timeout_seconds=(settings.multimodal_embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.multimodal_embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.multimodal_embedding.health_timeout_seconds),
    )

    vector_store = QdrantVectorStore(
        base_url=settings.qdrant.base_url,
        request_timeout_seconds=(settings.qdrant.request_timeout_seconds),
        health_timeout_seconds=(settings.qdrant.health_timeout_seconds),
    )

    office_converter = LibreOfficeNormativeOfficeToPdfConverter(
        executable=(settings.office_conversion.executable),
        timeout_seconds=(settings.office_conversion.timeout_seconds),
    )

    ollama_releaser = OllamaLoadedModelReleaser(
        base_url=settings.embedding.base_url,
        timeout_seconds=10.0,
    )

    use_case = IndexTechnicalAssignment(
        unit_of_work_factory=unit_of_work_factory,
        storage=storage,
        pdf_processor=(PyMuPdfTechnicalAssignmentProcessor()),
        embedding_provider=multimodal,
        vector_store=vector_store,
        office_converter=office_converter,
        collection=(settings.qdrant.multimodal_collection),
        output_dimension=(settings.multimodal_embedding.output_dimension),
        max_pages=(settings.technical_assignment.max_pages),
        render_dpi=(settings.technical_assignment.render_dpi),
        max_image_pixels=(settings.multimodal_embedding.max_image_pixels),
        page_text_limit=(settings.technical_assignment.page_text_limit),
    )

    try:
        await ollama_releaser.release_loaded(
            model_names=(settings.technical_assignment.ollama_models_to_release),
        )

        return await use_case.execute(
            technical_assignment_id=(technical_assignment_id),
            allow_retry=allow_retry,
        )

    finally:
        await engine.dispose()
