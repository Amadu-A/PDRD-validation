# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/technical_assignments.py

"""Use cases регистрации, чтения и preview ТЗ."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import (
    UUID,
    uuid4,
)
from zipfile import (
    BadZipFile,
    ZipFile,
)

from pdrd_knowledge_service.application.normative_document_formats import (
    is_word_mime_type,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
    NormativeDocumentStorageError,
)
from pdrd_knowledge_service.application.ports.office_conversion import (
    NormativeOfficeConversionError,
    NormativeOfficeToPdfConverter,
)
from pdrd_knowledge_service.application.ports.technical_assignment_persistence import (
    TechnicalAssignmentUnitOfWorkFactory,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    DOC_MIME_TYPE,
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
    resolve_technical_assignment_mime_type,
)
from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
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


class TechnicalAssignmentNotFoundError(
    LookupError,
):
    """ТЗ не найдено."""


class TechnicalAssignmentUploadError(
    ValueError,
):
    """Uploaded ТЗ не прошло validation."""


class TechnicalAssignmentRegistrationConflictError(
    RuntimeError,
):
    """Повторный technical_assignment_id имеет другое содержимое."""


class TechnicalAssignmentContentUnavailableError(
    RuntimeError,
):
    """Physical T content или preview недоступен."""


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentContent:
    """ТЗ в browser-viewable PDF representation."""

    assignment: TechnicalAssignment

    content: bytes

    mime_type: str


def utc_now() -> datetime:
    """Возвращает UTC now."""
    return datetime.now(
        UTC,
    )


def build_technical_assignment_storage_key(
    *,
    analysis_document_id: UUID,
    technical_assignment_id: UUID,
    original_name: str,
) -> str:
    """Строит deterministic internal storage key."""
    extension = Path(
        original_name,
    ).suffix.lower()

    return f"{analysis_document_id}/{technical_assignment_id}{extension}"


def _validate_content(
    *,
    content: bytes,
    original_name: str,
    max_upload_bytes: int,
) -> str:
    """Проверяет размер, extension и базовую signature."""
    if not content:
        raise TechnicalAssignmentUploadError(
            "Файл ТЗ пуст.",
        )

    if (
        len(
            content,
        )
        > max_upload_bytes
    ):
        raise TechnicalAssignmentUploadError(
            "Размер ТЗ превышает допустимый лимит.",
        )

    mime_type = resolve_technical_assignment_mime_type(
        original_name,
    )

    if mime_type == PDF_MIME_TYPE:
        if b"%PDF-" not in content[:_PDF_SIGNATURE_WINDOW]:
            raise TechnicalAssignmentUploadError(
                "ТЗ не содержит PDF signature.",
            )

        return mime_type

    if mime_type == DOC_MIME_TYPE:
        if not content.startswith(
            _DOC_SIGNATURE,
        ):
            raise TechnicalAssignmentUploadError(
                "ТЗ не содержит корректную DOC signature.",
            )

        return mime_type

    if mime_type == DOCX_MIME_TYPE:
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
            raise TechnicalAssignmentUploadError(
                "DOCX ТЗ не является корректным OOXML ZIP.",
            ) from error

        required = {
            "[Content_Types].xml",
            "word/document.xml",
        }

        if not required.issubset(
            names,
        ):
            raise TechnicalAssignmentUploadError(
                "DOCX ТЗ не содержит обязательную структуру Word-документа.",
            )

        return mime_type

    raise TechnicalAssignmentUploadError(
        "Формат ТЗ не поддерживается.",
    )


@dataclass(frozen=True, slots=True)
class RegisterTechnicalAssignment:
    """Идемпотентно регистрирует ТЗ и создаёт T-outbox event."""

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory

    storage: NormativeDocumentStorage

    max_upload_bytes: int

    clock: Clock = utc_now

    identifier_factory: IdentifierFactory = uuid4

    async def execute(
        self,
        *,
        technical_assignment_id: UUID,
        analysis_document_id: UUID,
        section_id: UUID,
        original_name: str,
        expected_sha256: str,
        content: bytes,
    ) -> TechnicalAssignment:
        """Сохраняет bytes и atomically создаёт queued record/outbox."""
        normalized_name = original_name.strip()

        mime_type = _validate_content(
            content=content,
            original_name=normalized_name,
            max_upload_bytes=self.max_upload_bytes,
        )

        actual_sha256 = sha256(
            content,
        ).hexdigest()

        if actual_sha256 != expected_sha256:
            raise TechnicalAssignmentUploadError(
                "SHA-256 uploaded ТЗ не совпадает с immutable analysis snapshot.",
            )

        async with self.unit_of_work_factory() as unit_of_work:
            existing = await unit_of_work.assignments.get(
                technical_assignment_id,
            )

            if existing is not None:
                self._validate_existing(
                    existing=existing,
                    analysis_document_id=analysis_document_id,
                    section_id=section_id,
                    original_name=normalized_name,
                    mime_type=mime_type,
                    size_bytes=len(
                        content,
                    ),
                    expected_sha256=expected_sha256,
                )

                return existing

            section = await unit_of_work.sections.get(
                section_id,
            )

            if section is None:
                raise TechnicalAssignmentUploadError(
                    f"Раздел {section_id} не найден.",
                )

        storage_key = build_technical_assignment_storage_key(
            analysis_document_id=analysis_document_id,
            technical_assignment_id=technical_assignment_id,
            original_name=normalized_name,
        )

        created_storage = False

        try:
            await self.storage.save(
                storage_key=storage_key,
                content=content,
            )

            created_storage = True

        except NormativeDocumentStorageError as save_error:
            try:
                existing_content = await self.storage.read(
                    storage_key=storage_key,
                )

            except NormativeDocumentStorageError as read_error:
                raise read_error from save_error

            if existing_content != content:
                raise TechnicalAssignmentRegistrationConflictError(
                    "Storage key ТЗ уже занят другим содержимым.",
                ) from save_error

        now = self.clock()

        uploaded = TechnicalAssignment(
            technical_assignment_id=technical_assignment_id,
            analysis_document_id=analysis_document_id,
            section_id=section_id,
            original_name=normalized_name,
            mime_type=mime_type,
            size_bytes=len(
                content,
            ),
            sha256=actual_sha256,
            index_status=TechnicalAssignmentIndexStatus.UPLOADED,
            index_error=None,
            indexed_at=None,
            created_at=now,
            updated_at=now,
        )

        queued = uploaded.transition_indexing(
            target_status=TechnicalAssignmentIndexStatus.QUEUED,
            changed_at=now,
        )

        message = TechnicalAssignmentOutboxMessage.index_requested(
            message_id=self.identifier_factory(),
            technical_assignment_id=technical_assignment_id,
            created_at=now,
        )

        try:
            async with self.unit_of_work_factory() as unit_of_work:
                await unit_of_work.assignments.add(
                    queued,
                )

                await unit_of_work.outbox.add(
                    message,
                )

                await unit_of_work.commit()

        except Exception:
            if created_storage:
                with suppress(
                    NormativeDocumentStorageError,
                ):
                    await self.storage.delete(
                        storage_key=storage_key,
                    )

            raise

        return queued

    @staticmethod
    def _validate_existing(
        *,
        existing: TechnicalAssignment,
        analysis_document_id: UUID,
        section_id: UUID,
        original_name: str,
        mime_type: str,
        size_bytes: int,
        expected_sha256: str,
    ) -> None:
        """Разрешает только идентичный idempotent retry."""
        expected = (
            analysis_document_id,
            section_id,
            original_name,
            mime_type,
            size_bytes,
            expected_sha256,
        )

        actual = (
            existing.analysis_document_id,
            existing.section_id,
            existing.original_name,
            existing.mime_type,
            existing.size_bytes,
            existing.sha256,
        )

        if actual != expected:
            raise TechnicalAssignmentRegistrationConflictError(
                "technical_assignment_id уже зарегистрирован "
                "с другим immutable snapshot.",
            )


@dataclass(frozen=True, slots=True)
class GetTechnicalAssignment:
    """Возвращает persisted T lifecycle."""

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory

    async def execute(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment:
        """Возвращает ТЗ либо application 404."""
        async with self.unit_of_work_factory() as unit_of_work:
            assignment = await unit_of_work.assignments.get(
                technical_assignment_id,
            )

        if assignment is None:
            raise TechnicalAssignmentNotFoundError(
                f"Техническое задание {technical_assignment_id} не найдено.",
            )

        return assignment


@dataclass(frozen=True, slots=True)
class GetTechnicalAssignmentContent:
    """Возвращает browser-viewable PDF ТЗ."""

    get_technical_assignment: GetTechnicalAssignment

    storage: NormativeDocumentStorage

    office_converter: NormativeOfficeToPdfConverter

    async def execute(
        self,
        *,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignmentContent:
        """Читает original и при необходимости конвертирует Word."""
        assignment = await self.get_technical_assignment.execute(
            technical_assignment_id=technical_assignment_id,
        )

        storage_key = build_technical_assignment_storage_key(
            analysis_document_id=assignment.analysis_document_id,
            technical_assignment_id=assignment.technical_assignment_id,
            original_name=assignment.original_name,
        )

        try:
            content = await self.storage.read(
                storage_key=storage_key,
            )

        except NormativeDocumentStorageError as error:
            raise TechnicalAssignmentContentUnavailableError(
                "Physical файл ТЗ недоступен.",
            ) from error

        if assignment.mime_type == PDF_MIME_TYPE:
            return TechnicalAssignmentContent(
                assignment=assignment,
                content=content,
                mime_type=PDF_MIME_TYPE,
            )

        if is_word_mime_type(
            assignment.mime_type,
        ):
            try:
                preview = await self.office_converter.convert_to_pdf(
                    content=content,
                    original_name=assignment.original_name,
                )

            except NormativeOfficeConversionError as error:
                raise TechnicalAssignmentContentUnavailableError(
                    "Не удалось сформировать PDF-preview ТЗ.",
                ) from error

            return TechnicalAssignmentContent(
                assignment=assignment,
                content=preview,
                mime_type=PDF_MIME_TYPE,
            )

        raise TechnicalAssignmentContentUnavailableError(
            "Формат ТЗ невозможно показать в браузере.",
        )
