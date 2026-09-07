# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/index_technical_assignment.py

"""Use case multimodal индексации технического задания."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)
from uuid import (
    NAMESPACE_URL,
    UUID,
    uuid5,
)

from pdrd_knowledge_service.application.normative_document_formats import (
    is_word_mime_type,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
    NormativeDocumentStorageError,
)
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
    MultimodalEmbeddingProvider,
    MultimodalEmbeddingProviderError,
    MultimodalEmbeddingTemporaryError,
)
from pdrd_knowledge_service.application.ports.office_conversion import (
    NormativeOfficeConversionError,
    NormativeOfficeToPdfConverter,
)
from pdrd_knowledge_service.application.ports.technical_assignment_pdf import (
    TechnicalAssignmentPdfProcessingError,
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
    TechnicalAssignmentNotFoundError,
    build_technical_assignment_storage_key,
)
from pdrd_knowledge_service.domain.project_context import (
    VectorRecord,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    extract_normative_references,
)

Clock = Callable[
    [],
    datetime,
]

_T_EMBEDDING_INSTRUCTION = (
    "Represent this technical assignment page for retrieval "
    "against Russian engineering project drawings, "
    "project requirements, equipment requirements and "
    "referenced normative documents."
)


class TechnicalAssignmentIndexingError(
    RuntimeError,
):
    """Терминальная ошибка T-indexing."""


class TechnicalAssignmentRetryableIndexingError(
    RuntimeError,
):
    """Временная ошибка T-indexing для Celery retry."""


def utc_now() -> datetime:
    """Возвращает UTC now."""
    return datetime.now(
        UTC,
    )


@dataclass(frozen=True, slots=True)
class IndexTechnicalAssignment:
    """Индексирует один T-document постранично."""

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory

    storage: NormativeDocumentStorage

    pdf_processor: TechnicalAssignmentPdfProcessor

    embedding_provider: MultimodalEmbeddingProvider

    vector_store: VectorStore

    office_converter: NormativeOfficeToPdfConverter

    collection: str

    output_dimension: int

    max_pages: int

    render_dpi: int

    max_image_pixels: int

    page_text_limit: int

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        technical_assignment_id: UUID,
        allow_retry: bool,
    ) -> TechnicalAssignment:
        """Выполняет bounded indexing одного ТЗ."""
        assignment = await self._mark_indexing(
            technical_assignment_id=technical_assignment_id,
        )

        if assignment.index_status is TechnicalAssignmentIndexStatus.READY:
            return assignment

        with suppress(
            VectorStoreError,
        ):
            await self.vector_store.delete_by_filter(
                collection=self.collection,
                key="technical_assignment_id",
                value=str(
                    technical_assignment_id,
                ),
            )

        try:
            raw_content = await self.storage.read(
                storage_key=build_technical_assignment_storage_key(
                    analysis_document_id=assignment.analysis_document_id,
                    technical_assignment_id=assignment.technical_assignment_id,
                    original_name=assignment.original_name,
                ),
            )

            pdf_content = await self._prepare_pdf(
                assignment=assignment,
                content=raw_content,
            )

            pages = await self.pdf_processor.extract_pages(
                content=pdf_content,
                max_pages=self.max_pages,
                render_dpi=self.render_dpi,
                max_image_pixels=self.max_image_pixels,
            )

            if not await self.vector_store.collection_exists(
                self.collection,
            ):
                await self.vector_store.create_collection(
                    collection=self.collection,
                    vector_size=self.output_dimension,
                )

            for page in pages:
                vectors = await self.embedding_provider.embed(
                    (
                        MultimodalEmbeddingInput(
                            text=page.text,
                            image_bytes=page.image_bytes,
                            instruction=_T_EMBEDDING_INSTRUCTION,
                        ),
                    )
                )

                if (
                    len(
                        vectors,
                    )
                    != 1
                ):
                    raise MultimodalEmbeddingProviderError(
                        "T page должна получить ровно один embedding.",
                    )

                vector = vectors[0]

                if (
                    len(
                        vector,
                    )
                    != self.output_dimension
                ):
                    raise MultimodalEmbeddingProviderError(
                        "T embedding имеет неправильную dimension.",
                    )

                point_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        (
                            "pdrd:technical-assignment:"
                            f"{technical_assignment_id}:"
                            f"page:{page.page_number}"
                        ),
                    )
                )

                await self.vector_store.upsert(
                    collection=self.collection,
                    records=(
                        VectorRecord(
                            point_id=point_id,
                            vector=vector,
                            payload={
                                "source_type": "technical_assignment",
                                "representation": "page_multimodal",
                                "technical_assignment_id": str(
                                    assignment.technical_assignment_id,
                                ),
                                "analysis_document_id": str(
                                    assignment.analysis_document_id,
                                ),
                                "section_id": str(
                                    assignment.section_id,
                                ),
                                "source_file": assignment.original_name,
                                "source_sha256": assignment.sha256,
                                "page": page.page_number,
                                "pixel_width": page.pixel_width,
                                "pixel_height": page.pixel_height,
                                "normative_refs": list(
                                    extract_normative_references(
                                        page.text,
                                    )
                                ),
                                "text": page.text[: self.page_text_limit],
                            },
                        ),
                    ),
                )

            return await self._mark_ready(
                technical_assignment_id=technical_assignment_id,
            )

        except (
            MultimodalEmbeddingTemporaryError,
            VectorStoreError,
            NormativeDocumentStorageError,
        ) as error:
            await self._cleanup_vectors(
                technical_assignment_id=technical_assignment_id,
            )

            if allow_retry:
                await self._mark_queued(
                    technical_assignment_id=technical_assignment_id,
                )

                raise TechnicalAssignmentRetryableIndexingError(
                    f"{type(error).__name__}: {error}",
                ) from error

            await self._mark_failed(
                technical_assignment_id=technical_assignment_id,
                error=error,
            )

            raise TechnicalAssignmentIndexingError(
                str(
                    error,
                )
            ) from error

        except (
            MultimodalEmbeddingProviderError,
            NormativeOfficeConversionError,
            TechnicalAssignmentPdfProcessingError,
        ) as error:
            await self._cleanup_vectors(
                technical_assignment_id=technical_assignment_id,
            )

            await self._mark_failed(
                technical_assignment_id=technical_assignment_id,
                error=error,
            )

            raise TechnicalAssignmentIndexingError(
                str(
                    error,
                )
            ) from error

        except Exception as error:
            await self._cleanup_vectors(
                technical_assignment_id=technical_assignment_id,
            )

            await self._mark_failed(
                technical_assignment_id=technical_assignment_id,
                error=error,
            )

            raise TechnicalAssignmentIndexingError(
                f"{type(error).__name__}: {error}",
            ) from error

        finally:
            with suppress(
                MultimodalEmbeddingProviderError,
            ):
                await self.embedding_provider.release()

    async def _prepare_pdf(
        self,
        *,
        assignment: TechnicalAssignment,
        content: bytes,
    ) -> bytes:
        """Нормализует Word ТЗ в PDF."""
        if assignment.mime_type == PDF_MIME_TYPE:
            return content

        if is_word_mime_type(
            assignment.mime_type,
        ):
            return await self.office_converter.convert_to_pdf(
                content=content,
                original_name=assignment.original_name,
            )

        raise TechnicalAssignmentIndexingError(
            "MIME type ТЗ не поддерживается.",
        )

    async def _mark_indexing(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment:
        """Переходит queued -> indexing либо продолжает redelivery."""
        async with self.unit_of_work_factory() as unit_of_work:
            assignment = await unit_of_work.assignments.get_for_update(
                technical_assignment_id,
            )

            if assignment is None:
                raise TechnicalAssignmentNotFoundError(
                    f"ТЗ {technical_assignment_id} не найдено.",
                )

            if assignment.index_status is TechnicalAssignmentIndexStatus.READY:
                return assignment

            if assignment.index_status is TechnicalAssignmentIndexStatus.INDEXING:
                return assignment

            if assignment.index_status is not TechnicalAssignmentIndexStatus.QUEUED:
                raise TechnicalAssignmentIndexingError(
                    "Нельзя начать T-indexing из состояния "
                    f"{assignment.index_status.value}.",
                )

            changed = assignment.transition_indexing(
                target_status=TechnicalAssignmentIndexStatus.INDEXING,
                changed_at=self.clock(),
            )

            await unit_of_work.assignments.update(
                changed,
            )

            await unit_of_work.commit()

            return changed

    async def _mark_ready(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment:
        """Фиксирует successful index."""
        async with self.unit_of_work_factory() as unit_of_work:
            assignment = await unit_of_work.assignments.get_for_update(
                technical_assignment_id,
            )

            if assignment is None:
                raise TechnicalAssignmentNotFoundError(
                    f"ТЗ {technical_assignment_id} не найдено.",
                )

            ready = assignment.transition_indexing(
                target_status=TechnicalAssignmentIndexStatus.READY,
                changed_at=self.clock(),
            )

            await unit_of_work.assignments.update(
                ready,
            )

            await unit_of_work.commit()

            return ready

    async def _mark_queued(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> None:
        """Возвращает transient failure в queued."""
        async with self.unit_of_work_factory() as unit_of_work:
            assignment = await unit_of_work.assignments.get_for_update(
                technical_assignment_id,
            )

            if assignment is None:
                return

            if assignment.index_status is not TechnicalAssignmentIndexStatus.INDEXING:
                return

            queued = assignment.transition_indexing(
                target_status=TechnicalAssignmentIndexStatus.QUEUED,
                changed_at=self.clock(),
            )

            await unit_of_work.assignments.update(
                queued,
            )

            await unit_of_work.commit()

    async def _mark_failed(
        self,
        *,
        technical_assignment_id: UUID,
        error: Exception,
    ) -> None:
        """Фиксирует terminal indexing failure."""
        async with self.unit_of_work_factory() as unit_of_work:
            assignment = await unit_of_work.assignments.get_for_update(
                technical_assignment_id,
            )

            if assignment is None:
                return

            if assignment.index_status is not TechnicalAssignmentIndexStatus.INDEXING:
                return

            failed = assignment.transition_indexing(
                target_status=TechnicalAssignmentIndexStatus.FAILED,
                changed_at=self.clock(),
                error=(f"{type(error).__name__}: {error}")[:2000],
            )

            await unit_of_work.assignments.update(
                failed,
            )

            await unit_of_work.commit()

    async def _cleanup_vectors(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> None:
        """Best-effort cleanup partial T points."""
        with suppress(
            VectorStoreError,
        ):
            await self.vector_store.delete_by_filter(
                collection=self.collection,
                key="technical_assignment_id",
                value=str(
                    technical_assignment_id,
                ),
            )
