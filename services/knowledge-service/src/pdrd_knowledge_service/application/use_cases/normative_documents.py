# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_documents.py

"""Прикладные сценарии документов управляемого каталога."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import (
    UTC,
    datetime,
)
from hashlib import sha256
from io import BytesIO
from uuid import (
    UUID,
    uuid4,
)
from zipfile import (
    BadZipFile,
    ZipFile,
)

from pdrd_knowledge_service.application.normative_document_formats import (
    DOC_EXTENSION,
    DOCX_EXTENSION,
    PDF_EXTENSION,
    PDF_MIME_TYPE,
    SUPPORTED_DOCUMENT_MIME_BY_EXTENSION,
    is_word_mime_type,
    preview_storage_key,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
    NormativeDocumentStorageError,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWork,
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.section_locks import (
    CatalogSectionLocks,
    InMemoryCatalogSectionLocks,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
    VectorStoreError,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
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

_DOC_SIGNATURE = bytes.fromhex(
    "D0CF11E0A1B11AE1",
)

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
    """Запрошенный документ каталога не найден."""


class NormativeDocumentUploadError(ValueError):
    """Некорректный загружаемый документ каталога."""


class NormativeDocumentCategoryError(ValueError):
    """Некорректная категория managed документа."""


class NormativeDocumentMutationConflictError(RuntimeError):
    """Текущее состояние документа запрещает изменение или удаление."""


class NormativeDocumentContentUnavailableError(RuntimeError):
    """PDF-preview документа пока недоступен."""


@dataclass(frozen=True, slots=True)
class NormativeDocumentContent:
    """Документ вместе с физическим содержимым."""

    document: NormativeDocument

    content: bytes

    mime_type: str


def utc_now() -> datetime:
    """Возвращает текущее время UTC с часовым поясом."""
    return datetime.now(
        UTC,
    )


async def _require_section(
    unit_of_work: NormativeCatalogUnitOfWork,
    section_id: UUID,
) -> None:
    """Проверяет существование раздела."""
    section = await unit_of_work.sections.get(
        section_id,
    )

    if section is None or section.deleting:
        raise NormativeSectionNotFoundError(
            f"Раздел нормативной базы {section_id} не найден.",
        )


async def _require_document(
    unit_of_work: NormativeCatalogUnitOfWork,
    document_id: UUID,
) -> NormativeDocument:
    """Возвращает документ каталога либо прикладную ошибку."""
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
    area: CatalogArea,
    owner_user_id: UUID | None = None,
) -> None:
    """Проверяет раздел и область категории документа."""
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

    if category.owner_user_id != owner_user_id:
        raise NormativeDocumentCategoryError("Категория принадлежит другому владельцу.")

    if category.area is not area:
        raise NormativeDocumentCategoryError(
            "Категория документа принадлежит другой области каталога.",
        )


def _normalize_document_name(
    original_name: str,
) -> tuple[
    str,
    str,
    str,
]:
    """Нормализует имя и определяет поддерживаемый формат."""
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

    lowered_name = file_name.lower()

    extension = next(
        (
            candidate
            for candidate in (
                DOCX_EXTENSION,
                PDF_EXTENSION,
                DOC_EXTENSION,
            )
            if lowered_name.endswith(
                candidate,
            )
        ),
        None,
    )

    if extension is None:
        raise NormativeDocumentUploadError(
            "Поддерживаются только PDF, DOC и DOCX.",
        )

    return (
        file_name,
        extension,
        SUPPORTED_DOCUMENT_MIME_BY_EXTENSION[extension],
    )


def _validate_pdf_content(
    content: bytes,
) -> None:
    """Проверяет PDF signature."""
    if b"%PDF-" not in content[:_PDF_SIGNATURE_WINDOW]:
        raise NormativeDocumentUploadError(
            "Загруженный файл не содержит PDF signature.",
        )


def _validate_doc_content(
    content: bytes,
) -> None:
    """Проверяет сигнатуру составного двоичного файла старого DOC."""
    if not content.startswith(
        _DOC_SIGNATURE,
    ):
        raise NormativeDocumentUploadError(
            "Загруженный файл не содержит корректную DOC signature.",
        )


def _validate_docx_content(
    content: bytes,
) -> None:
    """Проверяет базовую структуру OOXML DOCX."""
    try:
        with ZipFile(
            BytesIO(
                content,
            )
        ) as archive:
            names = set(
                archive.namelist(),
            )

    except (
        BadZipFile,
        OSError,
    ) as error:
        raise NormativeDocumentUploadError(
            "Загруженный DOCX не является корректным OOXML ZIP.",
        ) from error

    required_entries = {
        "[Content_Types].xml",
        "word/document.xml",
    }

    if not required_entries.issubset(
        names,
    ):
        raise NormativeDocumentUploadError(
            "DOCX не содержит обязательную структуру Word-документа.",
        )


def _validate_document_content(
    content: bytes,
    *,
    extension: str,
    max_upload_bytes: int,
) -> None:
    """Проверяет размер и signature загруженного документа."""
    if max_upload_bytes <= 0:
        raise NormativeDocumentUploadError(
            "Лимит размера upload настроен некорректно.",
        )

    if not content:
        raise NormativeDocumentUploadError(
            "Загружаемый нормативный документ пуст.",
        )

    if (
        len(
            content,
        )
        > max_upload_bytes
    ):
        raise NormativeDocumentUploadError(
            "Размер нормативного документа превышает допустимый лимит.",
        )

    if extension == PDF_EXTENSION:
        _validate_pdf_content(
            content,
        )

        return

    if extension == DOC_EXTENSION:
        _validate_doc_content(
            content,
        )

        return

    if extension == DOCX_EXTENSION:
        _validate_docx_content(
            content,
        )

        return

    raise NormativeDocumentUploadError(
        "Формат нормативного документа не поддерживается.",
    )


@dataclass(frozen=True, slots=True)
class ListNormativeDocuments:
    """Возвращает документы одной области каталога раздела."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        section_id: UUID,
        area: CatalogArea = CatalogArea.NORMATIVE,
        owner_user_id: UUID | None = None,
    ) -> tuple[
        NormativeDocument,
        ...,
    ]:
        """Возвращает документы вместе с статусом индексации."""
        async with self.unit_of_work_factory() as unit_of_work:
            await _require_section(
                unit_of_work,
                section_id,
            )

            documents = await unit_of_work.documents.list_by_section(
                section_id,
            )

        return tuple(
            document
            for document in documents
            if document.area is area
            and (owner_user_id is None or document.owner_user_id == owner_user_id)
        )


@dataclass(frozen=True, slots=True)
class GetNormativeDocument:
    """Возвращает метаданные одного managed документа."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocument:
        """Загружает документ по UUID."""
        async with self.unit_of_work_factory() as unit_of_work:
            return await _require_document(
                unit_of_work,
                document_id,
            )


@dataclass(frozen=True, slots=True)
class UploadNormativeDocument:
    """Сохраняет документ каталога и метаданные."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    storage: NormativeDocumentStorage

    max_upload_bytes: int

    section_locks: CatalogSectionLocks = field(
        default_factory=InMemoryCatalogSectionLocks
    )

    clock: Clock = utc_now

    identifier_factory: IdentifierFactory = uuid4

    async def execute(
        self,
        *,
        section_id: UUID,
        category_id: UUID | None,
        original_name: str,
        content: bytes,
        area: CatalogArea = CatalogArea.NORMATIVE,
        owner_user_id: UUID | None = None,
    ) -> NormativeDocument:
        """Не допускает удаления раздела между записью файла и SQL commit."""
        async with self.section_locks.shared(section_id):
            return await self._execute(
                section_id=section_id,
                category_id=category_id,
                original_name=original_name,
                content=content,
                area=area,
                owner_user_id=owner_user_id,
            )

    async def _execute(
        self,
        *,
        section_id: UUID,
        category_id: UUID | None,
        original_name: str,
        content: bytes,
        area: CatalogArea = CatalogArea.NORMATIVE,
        owner_user_id: UUID | None = None,
    ) -> NormativeDocument:
        """Валидирует документ и регистрирует метаданные."""
        (
            normalized_name,
            extension,
            mime_type,
        ) = _normalize_document_name(
            original_name,
        )

        _validate_document_content(
            content,
            extension=extension,
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
                area=area,
                owner_user_id=owner_user_id,
            )

        document_id = self.identifier_factory()

        created_at = self.clock()

        storage_key = f"{section_id}/{document_id}{extension}"

        document = NormativeDocument(
            document_id=document_id,
            section_id=section_id,
            category_id=category_id,
            original_name=normalized_name,
            storage_key=storage_key,
            mime_type=mime_type,
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
            area=area,
            owner_user_id=owner_user_id,
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
    """Возвращает доступное для просмотра в браузере содержимое managed документа."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    storage: NormativeDocumentStorage

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> NormativeDocumentContent:
        """Для Word возвращает готовый предпросмотр PDF."""
        async with self.unit_of_work_factory() as unit_of_work:
            document = await _require_document(
                unit_of_work,
                document_id,
            )

        storage_key = document.storage_key

        mime_type = document.mime_type

        if is_word_mime_type(
            document.mime_type,
        ):
            if document.index_status is not IndexingStatus.READY:
                raise NormativeDocumentContentUnavailableError(
                    "PDF-preview Word-документа станет доступен после индексации.",
                )

            storage_key = preview_storage_key(
                document.storage_key,
            )

            mime_type = PDF_MIME_TYPE

        content = await self.storage.read(
            storage_key=storage_key,
        )

        return NormativeDocumentContent(
            document=document,
            content=content,
            mime_type=mime_type,
        )


@dataclass(frozen=True, slots=True)
class MoveNormativeDocument:
    """Перемещает документ между категориями той же области каталога."""

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
        """Синхронизирует категорию в PostgreSQL и данных Qdrant."""
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
                area=document.area,
                owner_user_id=document.owner_user_id,
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
                                    if (document.category_id is not None)
                                    else None
                                )
                            },
                        )

                raise

        return moved_document


@dataclass(frozen=True, slots=True)
class DeleteNormativeDocument:
    """Идемпотентно удаляет документ из всех хранилищ каталога."""

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
        """Удаляет векторы Qdrant, исходный файл, предпросмотр и метаданные SQL."""
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

        if is_word_mime_type(
            document.mime_type,
        ):
            await self.storage.delete(
                storage_key=preview_storage_key(
                    document.storage_key,
                ),
            )

        async with self.unit_of_work_factory() as unit_of_work:
            await unit_of_work.documents.delete(
                document_id,
            )

            await unit_of_work.commit()

        return document_id


@dataclass(frozen=True, slots=True)
class NormativeDocumentUseCases:
    """Группирует операции документов каталога."""

    list_documents: ListNormativeDocuments

    get_document: GetNormativeDocument

    upload_document: UploadNormativeDocument

    get_document_content: GetNormativeDocumentContent

    move_document: MoveNormativeDocument

    delete_document: DeleteNormativeDocument
