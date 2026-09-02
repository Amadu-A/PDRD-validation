# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_documents.py

"""Use cases документов управляемой нормативной базы."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)
from hashlib import sha256
from uuid import (
    UUID,
    uuid4,
)

from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
    NormativeDocumentStorageError,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWork,
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
    VectorStoreError,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)

Clock = Callable[
    [],
    datetime,
]

IdentifierFactory = Callable[
    [],
    UUID,
]

_PDF_SIGNATURE_WINDOW = 1024

_MUTATION_BLOCKED_STATUSES = frozenset(
    {
        IndexingStatus.QUEUED,
        IndexingStatus.INDEXING,
        IndexingStatus.DELETING,
    }
)

_DELETE_BLOCKED_STATUSES = frozenset(
    {
        IndexingStatus.QUEUED,
        IndexingStatus.INDEXING,
    }
)


class NormativeDocumentNotFoundError(LookupError):
    """Запрошенный нормативный документ не найден."""


class NormativeDocumentUploadError(ValueError):
    """Некорректный загружаемый нормативный документ."""


class NormativeDocumentCategoryError(ValueError):
    """Некорректная категория нормативного документа."""


class NormativeDocumentMutationConflictError(RuntimeError):
    """Текущее состояние документа запрещает изменение или удаление."""


@dataclass(frozen=True, slots=True)
class NormativeDocumentContent:
    """Документ вместе с физическим содержимым."""

    document: NormativeDocument

    content: bytes


def utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время."""
    return datetime.now(
        UTC,
    )


async def _require_section(
    unit_of_work: NormativeCatalogUnitOfWork,
    section_id: UUID,
) -> None:
    """Проверяет существование нормативного раздела."""
    section = await unit_of_work.sections.get(
        section_id,
    )

    if section is None:
        raise NormativeSectionNotFoundError(
            f"Раздел нормативной базы {section_id} не найден.",
        )


async def _require_document(
    unit_of_work: NormativeCatalogUnitOfWork,
    document_id: UUID,
) -> NormativeDocument:
    """Возвращает нормативный документ либо application error."""
    document = await unit_of_work.documents.get(
        document_id,
    )

    if document is None:
        raise NormativeDocumentNotFoundError(
            f"Нормативный документ {document_id} не найден.",
        )

    return document


async def _validate_category(
    unit_of_work: NormativeCatalogUnitOfWork,
    *,
    section_id: UUID,
    category_id: UUID | None,
) -> None:
    """Проверяет принадлежность category указанному section."""
    if category_id is None:
        return

    category = await unit_of_work.categories.get(
        category_id,
    )

    if category is None:
        raise NormativeDocumentCategoryError(
            f"Категория нормативной базы {category_id} не найдена.",
        )

    if category.section_id != section_id:
        raise NormativeDocumentCategoryError(
            "Категория документа принадлежит другому разделу.",
        )


def _normalize_pdf_name(
    original_name: str,
) -> str:
    """Оставляет безопасное пользовательское имя PDF."""
    file_name = (
        original_name.replace(
            "\\",
            "/",
        )
        .rsplit(
            "/",
            maxsplit=1,
        )[-1]
        .strip()
    )

    if not file_name:
        raise NormativeDocumentUploadError(
            "Имя нормативного документа не может быть пустым.",
        )

    if "\x00" in file_name:
        raise NormativeDocumentUploadError(
            "Имя нормативного документа содержит NUL-символ.",
        )

    if not file_name.lower().endswith(
        ".pdf",
    ):
        raise NormativeDocumentUploadError(
            "На текущем этапе поддерживаются только PDF-файлы.",
        )

    return file_name


def _validate_pdf_content(
    content: bytes,
    *,
    max_upload_bytes: int,
) -> None:
    """Проверяет размер и PDF signature."""
    if max_upload_bytes <= 0:
        raise NormativeDocumentUploadError(
            "Лимит размера upload настроен некорректно.",
        )

    if not content:
        raise NormativeDocumentUploadError(
            "Загружаемый PDF пуст.",
        )

    if (
        len(
            content,
        )
        > max_upload_bytes
    ):
        raise NormativeDocumentUploadError(
            "Размер нормативного PDF превышает допустимый лимит.",
        )

    if b"%PDF-" not in content[:_PDF_SIGNATURE_WINDOW]:
        raise NormativeDocumentUploadError(
            "Загруженный файл не содержит PDF signature.",
        )


@dataclass(frozen=True, slots=True)
class ListNormativeDocuments:
    """Возвращает документы нормативного раздела."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocument,
        ...,
    ]:
        """Возвращает все documents вместе с indexing status."""
        async with self.unit_of_work_factory() as unit_of_work:
            await _require_section(
                unit_of_work,
                section_id,
            )

            documents = await unit_of_work.documents.list_by_section(
                section_id,
            )

        return tuple(
            documents,
        )


@dataclass(frozen=True, slots=True)
class GetNormativeDocument:
    """Возвращает metadata одного нормативного документа."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocument:
        """Загружает document по UUID."""
        async with self.unit_of_work_factory() as unit_of_work:
            return await _require_document(
                unit_of_work,
                document_id,
            )


@dataclass(frozen=True, slots=True)
class UploadNormativeDocument:
    """Сохраняет managed PDF и его metadata."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    storage: NormativeDocumentStorage

    max_upload_bytes: int

    clock: Clock = utc_now

    identifier_factory: IdentifierFactory = uuid4

    async def execute(
        self,
        *,
        section_id: UUID,
        category_id: UUID | None,
        original_name: str,
        content: bytes,
    ) -> NormativeDocument:
        """Валидирует PDF и атомарно регистрирует metadata."""
        normalized_name = _normalize_pdf_name(
            original_name,
        )

        _validate_pdf_content(
            content,
            max_upload_bytes=self.max_upload_bytes,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            await _require_section(
                unit_of_work,
                section_id,
            )

            await _validate_category(
                unit_of_work,
                section_id=section_id,
                category_id=category_id,
            )

        document_id = self.identifier_factory()

        created_at = self.clock()

        storage_key = f"{section_id}/{document_id}.pdf"

        document = NormativeDocument(
            document_id=document_id,
            section_id=section_id,
            category_id=category_id,
            original_name=normalized_name,
            storage_key=storage_key,
            mime_type="application/pdf",
            size_bytes=len(
                content,
            ),
            sha256=sha256(
                content,
            ).hexdigest(),
            index_status=IndexingStatus.UPLOADED,
            index_error=None,
            indexed_at=None,
            created_at=created_at,
            updated_at=created_at,
        )

        await self.storage.save(
            storage_key=storage_key,
            content=content,
        )

        try:
            async with self.unit_of_work_factory() as unit_of_work:
                await unit_of_work.documents.add(
                    document,
                )

                await unit_of_work.commit()

        except Exception:
            with suppress(
                NormativeDocumentStorageError,
            ):
                await self.storage.delete(
                    storage_key=storage_key,
                )

            raise

        return document


@dataclass(frozen=True, slots=True)
class GetNormativeDocumentContent:
    """Возвращает физическое содержимое managed документа."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    storage: NormativeDocumentStorage

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Загружает metadata и соответствующий physical file."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await _require_document(
                unit_of_work,
                document_id,
            )

        content = await self.storage.read(
            storage_key=document.storage_key,
        )

        return NormativeDocumentContent(
            document=document,
            content=content,
        )


@dataclass(frozen=True, slots=True)
class MoveNormativeDocument:
    """Перемещает документ между категориями того же раздела."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    vector_store: VectorStore

    collection: str

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        document_id: UUID,
        category_id: UUID | None,
    ) -> NormativeDocument:
        """Синхронизирует category в PostgreSQL и Qdrant payload."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get_for_update(
                document_id,
            )

            if document is None:
                raise NormativeDocumentNotFoundError(
                    f"Нормативный документ {document_id} не найден.",
                )

            if document.index_status in _MUTATION_BLOCKED_STATUSES:
                raise NormativeDocumentMutationConflictError(
                    "Документ нельзя перемещать из состояния "
                    f"{document.index_status.value}.",
                )

            await _validate_category(
                unit_of_work,
                section_id=document.section_id,
                category_id=category_id,
            )

            if document.category_id == category_id:
                return document

            moved_document = document.moved_to_category(
                category_id=category_id,
                changed_at=self.clock(),
            )

            payload_changed = False

            try:
                if document.index_status is IndexingStatus.READY:
                    await self.vector_store.set_payload_by_filter(
                        collection=self.collection,
                        key="document_id",
                        value=str(
                            document.document_id,
                        ),
                        payload={
                            "category_id": (
                                str(
                                    category_id,
                                )
                                if category_id is not None
                                else None
                            )
                        },
                    )

                    payload_changed = True

                await unit_of_work.documents.update(
                    moved_document,
                )

                await unit_of_work.commit()

            except Exception:
                if payload_changed:
                    with suppress(
                        VectorStoreError,
                    ):
                        await self.vector_store.set_payload_by_filter(
                            collection=self.collection,
                            key="document_id",
                            value=str(
                                document.document_id,
                            ),
                            payload={
                                "category_id": (
                                    str(
                                        document.category_id,
                                    )
                                    if document.category_id is not None
                                    else None
                                )
                            },
                        )

                raise

        return moved_document


@dataclass(frozen=True, slots=True)
class DeleteNormativeDocument:
    """Идемпотентно удаляет document из всех managed storages."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    storage: NormativeDocumentStorage

    vector_store: VectorStore

    collection: str

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> UUID:
        """Удаляет Qdrant points, physical PDF и PostgreSQL metadata."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await unit_of_work.documents.get_for_update(
                document_id,
            )

            if document is None:
                return document_id

            if document.index_status in _DELETE_BLOCKED_STATUSES:
                raise NormativeDocumentMutationConflictError(
                    "Документ нельзя удалить из состояния "
                    f"{document.index_status.value}.",
                )

            if document.index_status is not IndexingStatus.DELETING:
                document = document.transition_indexing(
                    target_status=IndexingStatus.DELETING,
                    changed_at=self.clock(),
                )

                await unit_of_work.documents.update(
                    document,
                )

                await unit_of_work.commit()

        await self.vector_store.delete_by_filter(
            collection=self.collection,
            key="document_id",
            value=str(
                document_id,
            ),
        )

        await self.storage.delete(
            storage_key=document.storage_key,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            await unit_of_work.documents.delete(
                document_id,
            )

            await unit_of_work.commit()

        return document_id


@dataclass(frozen=True, slots=True)
class NormativeDocumentUseCases:
    """Группирует operations нормативных документов."""

    list_documents: ListNormativeDocuments

    get_document: GetNormativeDocument

    upload_document: UploadNormativeDocument

    get_document_content: GetNormativeDocumentContent

    move_document: MoveNormativeDocument

    delete_document: DeleteNormativeDocument
