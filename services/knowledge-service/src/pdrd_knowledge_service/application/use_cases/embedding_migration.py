# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/embedding_migration.py

"""Blue/green переиндексация при изменении embedding identity."""

from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import (
    NAMESPACE_URL,
    uuid5,
)

from pdrd_knowledge_service.application.normative_document_formats import (
    PDF_MIME_TYPE,
    is_word_mime_type,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
)
from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
)
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProvider,
    MultimodalEmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.normative_pdf import (
    NormativePdfExtractor,
)
from pdrd_knowledge_service.application.ports.office_conversion import (
    NormativeOfficeToPdfConverter,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.technical_assignment_pdf import (
    TechnicalAssignmentPdfProcessor,
)
from pdrd_knowledge_service.application.ports.technical_assignment_persistence import (
    TechnicalAssignmentUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
    VectorStoreError,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    build_technical_assignment_storage_key,
)
from pdrd_knowledge_service.domain.embedding_index import (
    EmbeddingIndexPlan,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.normative_indexing import (
    chunk_normative_pages,
    stable_normative_point_id,
)
from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE as T_PDF_MIME_TYPE,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    extract_normative_references,
)

_T_EMBEDDING_INSTRUCTION = (
    "Represent this technical assignment page for retrieval "
    "against Russian engineering project drawings, "
    "project requirements, equipment requirements and "
    "referenced normative documents."
)


@dataclass(frozen=True, slots=True)
class EmbeddingMigrationResult:
    """Результат startup migration."""

    skipped: bool

    catalog_points: int

    technical_assignment_points: int

    experience_points: int


class EmbeddingCollectionRebuilder(Protocol):
    """Порт фактического наполнения физических collections."""

    async def rebuild_catalog(
        self,
        *,
        collection: str,
    ) -> int:
        """Переиндексирует persisted N/U catalog."""
        ...

    async def rebuild_technical_assignments(
        self,
        *,
        collection: str,
    ) -> int:
        """Переиндексирует persisted READY технические задания."""
        ...

    async def rebuild_experience(
        self,
        *,
        collection: str,
        source_collection: str | None,
    ) -> int:
        """Переиндексирует persisted experience payloads."""
        ...


@dataclass(slots=True)
class PersistedEmbeddingCollectionRebuilder:
    """Перестраивает indexes из durable project sources."""

    catalog_uow_factory: NormativeCatalogUnitOfWorkFactory

    technical_assignment_uow_factory: TechnicalAssignmentUnitOfWorkFactory

    catalog_storage: NormativeDocumentStorage

    technical_assignment_storage: NormativeDocumentStorage

    pdf_extractor: NormativePdfExtractor

    technical_assignment_pdf_processor: TechnicalAssignmentPdfProcessor

    office_converter: NormativeOfficeToPdfConverter

    text_embedding_provider: EmbeddingProvider

    multimodal_embedding_provider: MultimodalEmbeddingProvider

    vector_store: VectorStore

    output_dimension: int

    chunk_size: int

    chunk_overlap: int

    embed_batch_size: int

    upsert_batch_size: int

    technical_assignment_max_pages: int

    technical_assignment_render_dpi: int

    technical_assignment_max_image_pixels: int

    technical_assignment_page_text_limit: int

    async def rebuild_catalog(
        self,
        *,
        collection: str,
    ) -> int:
        """Переиндексирует persisted N и U files."""
        async with self.catalog_uow_factory() as unit_of_work:
            documents = await unit_of_work.documents.list_all()

        ready_documents = tuple(
            document
            for document in documents
            if document.index_status is IndexingStatus.READY
        )

        count = 0

        for document in ready_documents:
            count += await self._reindex_catalog_document(
                collection=collection,
                document=document,
            )

        return count

    async def _reindex_catalog_document(
        self,
        *,
        collection: str,
        document: NormativeDocument,
    ) -> int:
        """Переиндексирует один persisted N/U document."""
        raw = await self.catalog_storage.read(
            storage_key=document.storage_key,
        )

        if document.mime_type == PDF_MIME_TYPE:
            pdf_content = raw

        elif is_word_mime_type(
            document.mime_type,
        ):
            pdf_content = await self.office_converter.convert_to_pdf(
                content=raw,
                original_name=document.original_name,
            )

        else:
            raise RuntimeError(
                "Persisted managed document имеет "
                f"unsupported MIME: {document.mime_type}.",
            )

        pages = await self.pdf_extractor.extract_pages(
            content=pdf_content,
        )

        chunks = chunk_normative_pages(
            pages,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
        )

        inserted = 0

        for start in range(
            0,
            len(
                chunks,
            ),
            self.embed_batch_size,
        ):
            batch = chunks[start : start + self.embed_batch_size]

            vectors = await self.text_embedding_provider.embed(
                tuple(chunk.text for chunk in batch),
                instruction=None,
            )

            if len(
                vectors,
            ) != len(
                batch,
            ):
                raise RuntimeError(
                    "Catalog migration получила неправильное количество vectors.",
                )

            records = tuple(
                VectorRecord(
                    point_id=stable_normative_point_id(
                        document_id=document.document_id,
                        page_number=chunk.page_number,
                        chunk_index=chunk.chunk_index,
                    ),
                    vector=vector,
                    payload={
                        "document_id": str(
                            document.document_id,
                        ),
                        "section_id": str(
                            document.section_id,
                        ),
                        "category_id": (
                            str(
                                document.category_id,
                            )
                            if document.category_id is not None
                            else None
                        ),
                        "source_sha256": document.sha256,
                        "source_file": document.original_name,
                        "page": chunk.page_number,
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                    },
                )
                for chunk, vector in zip(
                    batch,
                    vectors,
                    strict=True,
                )
            )

            await self.vector_store.upsert(
                collection=collection,
                records=records,
            )

            inserted += len(
                records,
            )

        return inserted

    async def rebuild_technical_assignments(
        self,
        *,
        collection: str,
    ) -> int:
        """Переиндексирует persisted READY ТЗ."""
        async with self.technical_assignment_uow_factory() as unit_of_work:
            assignments = await unit_of_work.assignments.list_all()

        ready_assignments = tuple(
            assignment
            for assignment in assignments
            if (assignment.index_status is TechnicalAssignmentIndexStatus.READY)
        )

        inserted = 0

        for assignment in ready_assignments:
            try:
                inserted += await self._reindex_technical_assignment(
                    collection=collection,
                    assignment=assignment,
                )

            finally:
                with suppress(
                    MultimodalEmbeddingProviderError,
                ):
                    await self.multimodal_embedding_provider.release()

        return inserted

    async def _reindex_technical_assignment(
        self,
        *,
        collection: str,
        assignment: TechnicalAssignment,
    ) -> int:
        """Переиндексирует одно persisted техническое задание."""
        storage_key = build_technical_assignment_storage_key(
            analysis_document_id=(assignment.analysis_document_id),
            technical_assignment_id=(assignment.technical_assignment_id),
            original_name=assignment.original_name,
        )

        raw = await self.technical_assignment_storage.read(
            storage_key=storage_key,
        )

        if assignment.mime_type == T_PDF_MIME_TYPE:
            pdf_content = raw

        elif is_word_mime_type(
            assignment.mime_type,
        ):
            pdf_content = await self.office_converter.convert_to_pdf(
                content=raw,
                original_name=assignment.original_name,
            )

        else:
            raise RuntimeError(
                "Persisted ТЗ имеет unsupported MIME.",
            )

        pages = await self.technical_assignment_pdf_processor.extract_pages(
            content=pdf_content,
            max_pages=(self.technical_assignment_max_pages),
            render_dpi=(self.technical_assignment_render_dpi),
            max_image_pixels=(self.technical_assignment_max_image_pixels),
        )

        inserted = 0

        for page in pages:
            vectors = await self.multimodal_embedding_provider.embed(
                (
                    MultimodalEmbeddingInput(
                        text=page.text,
                        image_bytes=page.image_bytes,
                        instruction=(_T_EMBEDDING_INSTRUCTION),
                    ),
                )
            )

            if (
                len(
                    vectors,
                )
                != 1
                or len(
                    vectors[0],
                )
                != self.output_dimension
            ):
                raise RuntimeError(
                    "T migration получила invalid vector.",
                )

            point_id = str(
                uuid5(
                    NAMESPACE_URL,
                    (
                        "pdrd:technical-assignment:"
                        f"{assignment.technical_assignment_id}:"
                        f"page:{page.page_number}"
                    ),
                )
            )

            await self.vector_store.upsert(
                collection=collection,
                records=(
                    VectorRecord(
                        point_id=point_id,
                        vector=vectors[0],
                        payload={
                            "source_type": ("technical_assignment"),
                            "representation": ("page_multimodal"),
                            "technical_assignment_id": str(
                                assignment.technical_assignment_id,
                            ),
                            "analysis_document_id": str(
                                assignment.analysis_document_id,
                            ),
                            "section_id": str(
                                assignment.section_id,
                            ),
                            "source_file": (assignment.original_name),
                            "source_sha256": (assignment.sha256),
                            "page": page.page_number,
                            "pixel_width": page.pixel_width,
                            "pixel_height": (page.pixel_height),
                            "normative_refs": list(
                                extract_normative_references(
                                    page.text,
                                )
                            ),
                            "text": page.text[
                                : self.technical_assignment_page_text_limit
                            ],
                        },
                    ),
                ),
            )

            inserted += 1

        return inserted

    async def rebuild_experience(
        self,
        *,
        collection: str,
        source_collection: str | None,
    ) -> int:
        """Переэмбеддит persisted E payloads без старых vectors."""
        if source_collection is None:
            return 0

        source_points = await self.vector_store.scroll_payloads(
            collection=source_collection,
        )

        usable = tuple(
            point
            for point in source_points
            if str(
                point.payload.get(
                    "text",
                    "",
                )
            ).strip()
        )

        inserted = 0

        for start in range(
            0,
            len(
                usable,
            ),
            self.embed_batch_size,
        ):
            batch = usable[start : start + self.embed_batch_size]

            vectors = await self.text_embedding_provider.embed(
                tuple(str(point.payload["text"]) for point in batch),
                instruction=None,
            )

            if len(
                vectors,
            ) != len(
                batch,
            ):
                raise RuntimeError(
                    "Experience migration получила invalid vector count.",
                )

            records = tuple(
                VectorRecord(
                    point_id=point.point_id,
                    vector=vector,
                    payload=dict(
                        point.payload,
                    ),
                )
                for point, vector in zip(
                    batch,
                    vectors,
                    strict=True,
                )
            )

            await self.vector_store.upsert(
                collection=collection,
                records=records,
            )

            inserted += len(
                records,
            )

        return inserted


@dataclass(slots=True)
class MigrateEmbeddingIndexes:
    """Blue/green orchestration physical Qdrant indexes."""

    vector_store: VectorStore

    rebuilder: EmbeddingCollectionRebuilder

    plan: EmbeddingIndexPlan

    vector_size: int

    legacy_collections: tuple[str, ...]

    async def execute(
        self,
    ) -> EmbeddingMigrationResult:
        """Выполняет rebuild, atomic alias switch и cleanup."""
        current = {
            alias: await self.vector_store.get_alias_target(
                alias,
            )
            for alias in self.plan.aliases_to_targets
        }

        desired = self.plan.aliases_to_targets

        needs_rebuild = {
            alias: current[alias] != target for alias, target in desired.items()
        }

        if not any(
            needs_rebuild.values(),
        ):
            await self._cleanup_obsolete(
                keep=set(
                    desired.values(),
                ),
            )

            return EmbeddingMigrationResult(
                skipped=True,
                catalog_points=0,
                technical_assignment_points=0,
                experience_points=0,
            )

        created_targets: set[str] = set()

        catalog_points = 0

        technical_assignment_points = 0

        experience_points = 0

        experience_source = current[self.plan.experience_alias]

        if experience_source is None:
            experience_source = await self._find_legacy_experience_source()

        try:
            for alias, target in desired.items():
                if not needs_rebuild[alias]:
                    continue

                if await self.vector_store.collection_exists(
                    target,
                ):
                    await self.vector_store.delete_collection(
                        collection=target,
                    )

                await self.vector_store.create_collection(
                    collection=target,
                    vector_size=self.vector_size,
                )

                created_targets.add(
                    target,
                )

            if needs_rebuild[self.plan.catalog_alias]:
                catalog_points = await self.rebuilder.rebuild_catalog(
                    collection=(self.plan.catalog_target),
                )

            if needs_rebuild[self.plan.technical_assignment_alias]:
                technical_assignment_points = (
                    await self.rebuilder.rebuild_technical_assignments(
                        collection=(self.plan.technical_assignment_target),
                    )
                )

            if needs_rebuild[self.plan.experience_alias]:
                experience_points = await self.rebuilder.rebuild_experience(
                    collection=(self.plan.experience_target),
                    source_collection=(experience_source),
                )

            await self.vector_store.replace_aliases(
                {
                    alias: target
                    for alias, target in desired.items()
                    if needs_rebuild[alias]
                }
            )

        except Exception:
            for collection in created_targets:
                with suppress(
                    VectorStoreError,
                ):
                    await self.vector_store.delete_collection(
                        collection=collection,
                    )

            raise

        await self._cleanup_obsolete(
            keep=set(
                desired.values(),
            ),
        )

        return EmbeddingMigrationResult(
            skipped=False,
            catalog_points=catalog_points,
            technical_assignment_points=(technical_assignment_points),
            experience_points=experience_points,
        )

    async def _find_legacy_experience_source(
        self,
    ) -> str | None:
        """Находит старую E collection первого cutover."""
        collections = set(await self.vector_store.list_collections())

        for candidate in self.legacy_collections:
            if "experience" in candidate and candidate in collections:
                return candidate

        return None

    async def _cleanup_obsolete(
        self,
        *,
        keep: set[str],
    ) -> None:
        """Удаляет только managed obsolete physical collections."""
        collections = await self.vector_store.list_collections()

        managed_prefixes = (
            f"{self.plan.catalog_prefix}_",
            (f"{self.plan.technical_assignment_prefix}_"),
            f"{self.plan.experience_prefix}_",
        )

        legacy = set(
            self.legacy_collections,
        )

        for collection in collections:
            if collection in keep:
                continue

            if collection in legacy or collection.startswith(
                managed_prefixes,
            ):
                await self.vector_store.delete_collection(
                    collection=collection,
                )
