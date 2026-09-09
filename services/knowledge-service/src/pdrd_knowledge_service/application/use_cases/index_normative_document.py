# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/index_normative_document.py

"""Use case полной индексации managed нормативного документа."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)
from uuid import UUID

from pdrd_knowledge_service.application.normative_document_formats import (
    PDF_MIME_TYPE,
    is_word_mime_type,
    preview_storage_key,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
)
from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
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
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
    VectorStoreError,
)
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    NormativeDocumentNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.normative_indexing import (
    NormativeChunk,
    NormativeIndexingPreparationError,
    chunk_normative_pages,
    stable_normative_point_id,
)
from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)

Clock = Callable[
    [],
    datetime,
]


class NormativeIndexingStateError(RuntimeError):
    """Документ находится в состоянии, несовместимом с worker."""


class NormativeIndexingExecutionError(RuntimeError):
    """Нормативный документ не удалось проиндексировать."""


def utc_now() -> datetime:
    """Возвращает текущее UTC время."""
    return datetime.now(
        UTC,
    )


@dataclass(frozen=True, slots=True)
class IndexNormativeDocument:
    """Нормализует документ, строит embeddings и Qdrant points."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    storage: NormativeDocumentStorage

    pdf_extractor: NormativePdfExtractor

    embedding_provider: EmbeddingProvider

    vector_store: VectorStore

    collection: str

    chunk_size: int

    chunk_overlap: int

    embed_batch_size: int

    upsert_batch_size: int

    office_converter: NormativeOfficeToPdfConverter | None = None

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocument:
        """Выполняет idempotent indexing lifecycle документа."""
        document, should_process = await self._start_indexing(
            document_id=document_id,
        )

        if not should_process:
            return document

        try:
            content = await self.storage.read(
                storage_key=document.storage_key,
            )

            pdf_content = await self._prepare_pdf_content(
                document=document,
                content=content,
            )

            pages = await self.pdf_extractor.extract_pages(
                content=pdf_content,
            )

            chunks = chunk_normative_pages(
                pages,
                chunk_size=self.chunk_size,
                overlap=self.chunk_overlap,
            )

            if not chunks:
                raise NormativeIndexingPreparationError(
                    "Документ не содержит извлекаемого текста для индексации.",
                )

            if self.embed_batch_size <= 0:
                raise NormativeIndexingPreparationError(
                    "embed_batch_size должен быть положительным.",
                )

            if self.upsert_batch_size <= 0:
                raise NormativeIndexingPreparationError(
                    "upsert_batch_size должен быть положительным.",
                )

            await self.vector_store.delete_by_filter(
                collection=self.collection,
                key="document_id",
                value=str(
                    document.document_id,
                ),
            )

            records = await self._build_vector_records(
                document=document,
                chunks=chunks,
            )

            for start in range(
                0,
                len(
                    records,
                ),
                self.upsert_batch_size,
            ):
                await self.vector_store.upsert(
                    collection=self.collection,
                    records=records[start : start + self.upsert_batch_size],
                )

            return await self._mark_ready(
                document_id=document_id,
            )

        except Exception as error:
            with suppress(
                VectorStoreError,
            ):
                await self.vector_store.delete_by_filter(
                    collection=self.collection,
                    key="document_id",
                    value=str(
                        document.document_id,
                    ),
                )

            await self._mark_failed(
                document_id=document_id,
                error=error,
            )

            raise NormativeIndexingExecutionError(
                "Не удалось проиндексировать нормативный документ "
                f"{document_id}: {type(error).__name__}: {error}",
            ) from error

    async def _prepare_pdf_content(
        self,
        *,
        document: NormativeDocument,
        content: bytes,
    ) -> bytes:
        """Возвращает PDF bytes для общего page extraction pipeline."""
        if document.mime_type == PDF_MIME_TYPE:
            return content

        if not is_word_mime_type(
            document.mime_type,
        ):
            raise NormativeIndexingPreparationError(
                "MIME type нормативного документа не поддерживается.",
            )

        if self.office_converter is None:
            raise NormativeIndexingPreparationError(
                "Word → PDF converter не настроен.",
            )

        pdf_content = await self.office_converter.convert_to_pdf(
            content=content,
            original_name=document.original_name,
        )

        preview_key = preview_storage_key(
            document.storage_key,
        )

        await self.storage.delete(
            storage_key=preview_key,
        )

        await self.storage.save(
            storage_key=preview_key,
            content=pdf_content,
        )

        return pdf_content

    async def _build_vector_records(
        self,
        *,
        document: NormativeDocument,
        chunks: tuple[
            NormativeChunk,
            ...,
        ],
    ) -> tuple[
        VectorRecord,
        ...,
    ]:
        """Строит deterministic Qdrant records."""
        records: list[VectorRecord] = []

        for start in range(
            0,
            len(
                chunks,
            ),
            self.embed_batch_size,
        ):
            batch = chunks[start : start + self.embed_batch_size]

            vectors = await self.embedding_provider.embed(
                tuple(chunk.text for chunk in batch),
                instruction=None,
            )

            if len(
                vectors,
            ) != len(
                batch,
            ):
                raise NormativeIndexingPreparationError(
                    "Количество embeddings не совпадает "
                    "с количеством нормативных chunks.",
                )

            for chunk, vector in zip(
                batch,
                vectors,
                strict=True,
            ):
                records.append(
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
                )

        return tuple(
            records,
        )

    async def _start_indexing(
        self,
        *,
        document_id: UUID,
    ) -> tuple[
        NormativeDocument,
        bool,
    ]:
        """Переводит queued document в indexing либо восстанавливает retry."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get(
                document_id,
            )

            if document is None:
                raise NormativeDocumentNotFoundError(
                    f"Нормативный документ {document_id} не найден.",
                )

            if document.index_status in {
                IndexingStatus.READY,
                IndexingStatus.FAILED,
            }:
                return (
                    document,
                    False,
                )

            if document.index_status is IndexingStatus.INDEXING:
                return (
                    document,
                    True,
                )

            if document.index_status is not IndexingStatus.QUEUED:
                raise NormativeIndexingStateError(
                    "Worker не может индексировать документ "
                    f"из состояния {document.index_status.value}.",
                )

            indexing_document = document.transition_indexing(
                target_status=IndexingStatus.INDEXING,
                changed_at=self.clock(),
            )

            await unit_of_work.documents.update(
                indexing_document,
            )

            await unit_of_work.commit()

        return (
            indexing_document,
            True,
        )

    async def _mark_ready(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocument:
        """Фиксирует успешную индексацию."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get(
                document_id,
            )

            if document is None:
                raise NormativeDocumentNotFoundError(
                    f"Нормативный документ {document_id} не найден.",
                )

            if document.index_status is IndexingStatus.READY:
                return document

            if document.index_status is not IndexingStatus.INDEXING:
                raise NormativeIndexingStateError(
                    "Документ нельзя перевести в ready "
                    f"из состояния {document.index_status.value}.",
                )

            ready_document = document.transition_indexing(
                target_status=IndexingStatus.READY,
                changed_at=self.clock(),
            )

            await unit_of_work.documents.update(
                ready_document,
            )

            await unit_of_work.commit()

        return ready_document

    async def _mark_failed(
        self,
        *,
        document_id: UUID,
        error: Exception,
    ) -> NormativeDocument:
        """Фиксирует ошибку индексации, если document ещё indexing."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get(
                document_id,
            )

            if document is None:
                raise NormativeDocumentNotFoundError(
                    f"Нормативный документ {document_id} не найден.",
                )

            if document.index_status is not IndexingStatus.INDEXING:
                return document

            error_message = (f"{type(error).__name__}: {error}")[:2000]

            failed_document = document.transition_indexing(
                target_status=IndexingStatus.FAILED,
                changed_at=self.clock(),
                error=error_message,
            )

            await unit_of_work.documents.update(
                failed_document,
            )

            await unit_of_work.commit()

        return failed_document
