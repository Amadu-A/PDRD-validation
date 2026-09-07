# services/knowledge-service/src/pdrd_knowledge_service/core/container.py

"""Composition root Knowledge Service."""

from dataclasses import dataclass
from functools import partial
from pathlib import Path

from pdrd_knowledge_service.application.normative_catalog_defaults import (
    DEFAULT_SECTION_SYSTEM_PROMPT,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.use_cases.experience import (
    SearchExperience,
)
from pdrd_knowledge_service.application.use_cases.health import (
    CheckReadiness,
)
from pdrd_knowledge_service.application.use_cases.normative import (
    SearchNormative,
)
from pdrd_knowledge_service.application.use_cases.normative_categories import (
    CreateNormativeCategory,
    DeleteNormativeCategory,
    GetNormativeCategory,
    ListNormativeCategories,
    NormativeCategoryUseCases,
    UpdateNormativeCategory,
)
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    DeleteNormativeDocument,
    GetNormativeDocument,
    GetNormativeDocumentContent,
    ListNormativeDocuments,
    MoveNormativeDocument,
    NormativeDocumentUseCases,
    UploadNormativeDocument,
)
from pdrd_knowledge_service.application.use_cases.normative_indexing_queue import (
    QueueNormativeDocument,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    CreateNormativeSection,
    DeleteNormativeSection,
    GetNormativeSection,
    ListNormativeSections,
    NormativeSectionUseCases,
    UpdateNormativeSection,
)
from pdrd_knowledge_service.application.use_cases.project_context import (
    CreateProjectContext,
    DeleteProjectContext,
    SearchProjectContext,
)
from pdrd_knowledge_service.application.use_cases.technical_assignment_retrieval import (
    SearchTechnicalAssignment,
    SearchTechnicalAssignmentGuidedNormative,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    GetTechnicalAssignment,
    GetTechnicalAssignmentContent,
    RegisterTechnicalAssignment,
)
from pdrd_knowledge_service.application.use_cases.user_packages import (
    SearchUserPackages,
)
from pdrd_knowledge_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.health import (
    DatabaseReadinessProbe,
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
from pdrd_knowledge_service.infrastructure.embedding.ollama import (
    OllamaEmbeddingProvider,
)
from pdrd_knowledge_service.infrastructure.office.libreoffice import (
    LibreOfficeNormativeOfficeToPdfConverter,
)
from pdrd_knowledge_service.infrastructure.storage.filesystem import (
    LocalFilesystemNormativeDocumentStorage,
)
from pdrd_knowledge_service.infrastructure.vector_store.qdrant import (
    QdrantVectorStore,
)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит runtime dependencies Knowledge Service."""

    settings: Settings

    search_normative: SearchNormative

    search_user_packages: SearchUserPackages

    search_experience: SearchExperience

    check_readiness: CheckReadiness

    normative_catalog_uow_factory: NormativeCatalogUnitOfWorkFactory | None = None

    normative_sections: NormativeSectionUseCases | None = None

    normative_categories: NormativeCategoryUseCases | None = None

    normative_documents: NormativeDocumentUseCases | None = None

    queue_normative_document: QueueNormativeDocument | None = None

    register_technical_assignment: RegisterTechnicalAssignment | None = None

    get_technical_assignment: GetTechnicalAssignment | None = None

    get_technical_assignment_content: GetTechnicalAssignmentContent | None = None

    search_technical_assignment_guided: (
        SearchTechnicalAssignmentGuidedNormative | None
    ) = None

    create_project_context: CreateProjectContext | None = None

    search_project_context: SearchProjectContext | None = None

    delete_project_context: DeleteProjectContext | None = None


def build_container() -> ApplicationContainer:
    """Собирает concrete dependencies сервиса."""
    settings = get_settings()

    database_engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        database_engine,
    )

    normative_catalog_uow_factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork,
        session_factory,
    )

    technical_assignment_uow_factory = partial(
        SqlAlchemyTechnicalAssignmentUnitOfWork,
        session_factory,
    )

    document_storage = LocalFilesystemNormativeDocumentStorage(
        root_path=settings.storage.root_path,
    )

    technical_assignment_storage = LocalFilesystemNormativeDocumentStorage(
        root_path=Path(
            settings.technical_assignment.storage_root_path,
        ),
    )

    office_converter = LibreOfficeNormativeOfficeToPdfConverter()

    vector_store = QdrantVectorStore(
        base_url=settings.qdrant.base_url,
        request_timeout_seconds=settings.qdrant.request_timeout_seconds,
        health_timeout_seconds=settings.qdrant.health_timeout_seconds,
    )

    normative_sections = NormativeSectionUseCases(
        list_sections=ListNormativeSections(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        get_section=GetNormativeSection(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        create_section=CreateNormativeSection(
            unit_of_work_factory=normative_catalog_uow_factory,
            default_system_prompt=DEFAULT_SECTION_SYSTEM_PROMPT,
        ),
        update_section=UpdateNormativeSection(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        delete_section=DeleteNormativeSection(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
    )

    normative_categories = NormativeCategoryUseCases(
        list_categories=ListNormativeCategories(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        get_category=GetNormativeCategory(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        create_category=CreateNormativeCategory(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        update_category=UpdateNormativeCategory(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        delete_category=DeleteNormativeCategory(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
    )

    normative_documents = NormativeDocumentUseCases(
        list_documents=ListNormativeDocuments(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        get_document=GetNormativeDocument(
            unit_of_work_factory=normative_catalog_uow_factory,
        ),
        upload_document=UploadNormativeDocument(
            unit_of_work_factory=normative_catalog_uow_factory,
            storage=document_storage,
            max_upload_bytes=settings.storage.max_upload_bytes,
        ),
        get_document_content=GetNormativeDocumentContent(
            unit_of_work_factory=normative_catalog_uow_factory,
            storage=document_storage,
        ),
        move_document=MoveNormativeDocument(
            unit_of_work_factory=normative_catalog_uow_factory,
            vector_store=vector_store,
            collection=settings.qdrant.normative_collection,
        ),
        delete_document=DeleteNormativeDocument(
            unit_of_work_factory=normative_catalog_uow_factory,
            storage=document_storage,
            vector_store=vector_store,
            collection=settings.qdrant.normative_collection,
        ),
    )

    queue_normative_document = QueueNormativeDocument(
        unit_of_work_factory=normative_catalog_uow_factory,
    )

    database_probe = DatabaseReadinessProbe(
        engine=database_engine,
        timeout_seconds=settings.database.health_timeout_seconds,
    )

    embedding_provider = OllamaEmbeddingProvider(
        base_url=settings.embedding.base_url,
        model=settings.embedding.model,
        request_timeout_seconds=settings.embedding.request_timeout_seconds,
        connect_timeout_seconds=settings.embedding.connect_timeout_seconds,
        health_timeout_seconds=settings.embedding.health_timeout_seconds,
    )

    multimodal_embedding_provider = HttpMultimodalEmbeddingProvider(
        base_url=settings.multimodal_embedding.base_url,
        request_timeout_seconds=(settings.multimodal_embedding.request_timeout_seconds),
        connect_timeout_seconds=(settings.multimodal_embedding.connect_timeout_seconds),
        health_timeout_seconds=(settings.multimodal_embedding.health_timeout_seconds),
    )

    search_normative = SearchNormative(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        collection=settings.qdrant.normative_collection,
        embedding_model=settings.embedding.model,
        top_k=settings.search.normative_top_k,
        max_sources=settings.search.normative_max_sources,
        unit_of_work_factory=normative_catalog_uow_factory,
    )

    search_technical_assignment = SearchTechnicalAssignment(
        embedding_provider=multimodal_embedding_provider,
        vector_store=vector_store,
        unit_of_work_factory=technical_assignment_uow_factory,
        collection=settings.qdrant.multimodal_collection,
        embedding_model=settings.multimodal_embedding.model,
        top_k=settings.technical_assignment.retrieval_top_k,
        max_sources=settings.technical_assignment.max_sources,
    )

    search_technical_assignment_guided = SearchTechnicalAssignmentGuidedNormative(
        search_technical_assignment=search_technical_assignment,
        search_normative=search_normative,
        normative_catalog_uow_factory=normative_catalog_uow_factory,
        combined_max_sources=settings.search.normative_max_sources,
    )

    search_user_packages = SearchUserPackages(
        managed_search=search_normative,
    )

    search_experience = SearchExperience(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        collection=settings.qdrant.experience_collection,
        embedding_model=settings.embedding.model,
        top_k=settings.search.experience_top_k,
    )

    check_readiness = CheckReadiness(
        database_probe=database_probe,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        normative_collection=settings.qdrant.normative_collection,
        experience_collection=settings.qdrant.experience_collection,
    )

    get_technical_assignment = GetTechnicalAssignment(
        unit_of_work_factory=technical_assignment_uow_factory,
    )

    project_settings = settings.project_context

    return ApplicationContainer(
        settings=settings,
        search_normative=search_normative,
        search_user_packages=search_user_packages,
        search_experience=search_experience,
        check_readiness=check_readiness,
        normative_catalog_uow_factory=normative_catalog_uow_factory,
        normative_sections=normative_sections,
        normative_categories=normative_categories,
        normative_documents=normative_documents,
        queue_normative_document=queue_normative_document,
        register_technical_assignment=RegisterTechnicalAssignment(
            unit_of_work_factory=technical_assignment_uow_factory,
            storage=technical_assignment_storage,
            max_upload_bytes=settings.technical_assignment.max_upload_bytes,
        ),
        get_technical_assignment=get_technical_assignment,
        get_technical_assignment_content=GetTechnicalAssignmentContent(
            get_technical_assignment=get_technical_assignment,
            storage=technical_assignment_storage,
            office_converter=office_converter,
        ),
        search_technical_assignment_guided=search_technical_assignment_guided,
        create_project_context=CreateProjectContext(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            collection_prefix=project_settings.collection_prefix,
            embedding_model=settings.embedding.model,
            chunk_size=project_settings.chunk_size,
            chunk_overlap=project_settings.chunk_overlap,
            embed_batch_size=project_settings.embed_batch_size,
            upsert_batch_size=project_settings.upsert_batch_size,
        ),
        search_project_context=SearchProjectContext(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            collection_prefix=project_settings.collection_prefix,
            embedding_model=settings.embedding.model,
            top_k=project_settings.top_k,
        ),
        delete_project_context=DeleteProjectContext(
            vector_store=vector_store,
            collection_prefix=project_settings.collection_prefix,
        ),
    )
