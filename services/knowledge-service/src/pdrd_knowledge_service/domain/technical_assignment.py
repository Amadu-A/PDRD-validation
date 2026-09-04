# services/knowledge-service/src/pdrd_knowledge_service/domain/technical_assignment.py

"""Domain-модель технического задания конкретного анализа."""

import re
from dataclasses import (
    dataclass,
    replace,
)
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

PDF_MIME_TYPE = "application/pdf"

DOC_MIME_TYPE = "application/msword"

DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

_SUPPORTED_MIME_BY_EXTENSION = {
    ".pdf": PDF_MIME_TYPE,
    ".doc": DOC_MIME_TYPE,
    ".docx": DOCX_MIME_TYPE,
}

_SHA256_PATTERN = re.compile(
    r"^[0-9a-f]{64}$",
)


class TechnicalAssignmentError(ValueError):
    """Нарушение domain-инварианта ТЗ."""


class TechnicalAssignmentIndexStatus(StrEnum):
    """Состояние ТЗ относительно multimodal index."""

    UPLOADED = "uploaded"

    QUEUED = "queued"

    INDEXING = "indexing"

    READY = "ready"

    FAILED = "failed"

    DELETING = "deleting"


_ALLOWED_TRANSITIONS: dict[
    TechnicalAssignmentIndexStatus,
    frozenset[TechnicalAssignmentIndexStatus],
] = {
    TechnicalAssignmentIndexStatus.UPLOADED: frozenset(
        {
            TechnicalAssignmentIndexStatus.QUEUED,
            TechnicalAssignmentIndexStatus.DELETING,
        }
    ),
    TechnicalAssignmentIndexStatus.QUEUED: frozenset(
        {
            TechnicalAssignmentIndexStatus.INDEXING,
            TechnicalAssignmentIndexStatus.DELETING,
        }
    ),
    TechnicalAssignmentIndexStatus.INDEXING: frozenset(
        {
            TechnicalAssignmentIndexStatus.READY,
            TechnicalAssignmentIndexStatus.FAILED,
            TechnicalAssignmentIndexStatus.DELETING,
        }
    ),
    TechnicalAssignmentIndexStatus.READY: frozenset(
        {
            TechnicalAssignmentIndexStatus.QUEUED,
            TechnicalAssignmentIndexStatus.DELETING,
        }
    ),
    TechnicalAssignmentIndexStatus.FAILED: frozenset(
        {
            TechnicalAssignmentIndexStatus.QUEUED,
            TechnicalAssignmentIndexStatus.DELETING,
        }
    ),
    TechnicalAssignmentIndexStatus.DELETING: frozenset(),
}


def resolve_technical_assignment_mime_type(
    original_name: str,
) -> str:
    """Определяет доверенный MIME type по расширению файла."""
    extension = Path(
        original_name,
    ).suffix.lower()

    try:
        return _SUPPORTED_MIME_BY_EXTENSION[extension]

    except KeyError as error:
        raise TechnicalAssignmentError(
            "ТЗ поддерживает только PDF, DOC или DOCX.",
        ) from error


def _validate_datetime(
    value: datetime,
    *,
    field_name: str,
) -> None:
    """Требует timezone-aware datetime."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise TechnicalAssignmentError(
            f"{field_name} должен содержать timezone.",
        )


@dataclass(frozen=True, slots=True)
class TechnicalAssignment:
    """Описывает одно ТЗ, принадлежащее конкретному анализу."""

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    original_name: str

    mime_type: str

    size_bytes: int

    sha256: str

    index_status: TechnicalAssignmentIndexStatus

    index_error: str | None

    indexed_at: datetime | None

    created_at: datetime

    updated_at: datetime

    def __post_init__(
        self,
    ) -> None:
        """Проверяет domain-инварианты ТЗ."""
        normalized_name = self.original_name.strip()

        if not normalized_name:
            raise TechnicalAssignmentError(
                "Имя файла ТЗ не может быть пустым.",
            )

        if (
            "\x00" in normalized_name
            or "/" in normalized_name
            or "\\" in normalized_name
        ):
            raise TechnicalAssignmentError(
                "Имя файла ТЗ содержит недопустимый путь.",
            )

        expected_mime_type = resolve_technical_assignment_mime_type(
            normalized_name,
        )

        if self.mime_type != expected_mime_type:
            raise TechnicalAssignmentError(
                "MIME type ТЗ не соответствует расширению файла.",
            )

        if self.size_bytes <= 0:
            raise TechnicalAssignmentError(
                "Размер файла ТЗ должен быть положительным.",
            )

        if not _SHA256_PATTERN.fullmatch(
            self.sha256,
        ):
            raise TechnicalAssignmentError(
                "SHA-256 ТЗ должен содержать 64 lowercase hex-символа.",
            )

        _validate_datetime(
            self.created_at,
            field_name="created_at",
        )

        _validate_datetime(
            self.updated_at,
            field_name="updated_at",
        )

        if self.updated_at < self.created_at:
            raise TechnicalAssignmentError(
                "updated_at не может быть раньше created_at.",
            )

        if self.indexed_at is not None:
            _validate_datetime(
                self.indexed_at,
                field_name="indexed_at",
            )

        if (
            self.index_status is TechnicalAssignmentIndexStatus.READY
            and self.indexed_at is None
        ):
            raise TechnicalAssignmentError(
                "READY ТЗ должен содержать indexed_at.",
            )

        if (
            self.index_status is not TechnicalAssignmentIndexStatus.READY
            and self.indexed_at is not None
        ):
            raise TechnicalAssignmentError(
                "indexed_at допустим только для READY ТЗ.",
            )

        if self.index_status is TechnicalAssignmentIndexStatus.FAILED:
            if self.index_error is None or not self.index_error.strip():
                raise TechnicalAssignmentError(
                    "FAILED ТЗ должен содержать index_error.",
                )

        elif self.index_error is not None:
            raise TechnicalAssignmentError(
                "index_error допустим только для FAILED ТЗ.",
            )

    def transition_indexing(
        self,
        *,
        target_status: TechnicalAssignmentIndexStatus,
        changed_at: datetime,
        error: str | None = None,
    ) -> "TechnicalAssignment":
        """Возвращает ТЗ в следующем состоянии lifecycle."""
        _validate_datetime(
            changed_at,
            field_name="changed_at",
        )

        if changed_at < self.updated_at:
            raise TechnicalAssignmentError(
                "changed_at не может быть раньше updated_at.",
            )

        allowed = _ALLOWED_TRANSITIONS[self.index_status]

        if target_status not in allowed:
            raise TechnicalAssignmentError(
                "Недопустимый переход ТЗ: "
                f"{self.index_status.value} -> "
                f"{target_status.value}.",
            )

        normalized_error = None

        if target_status is TechnicalAssignmentIndexStatus.FAILED:
            normalized_error = (
                error.strip()
                if isinstance(
                    error,
                    str,
                )
                else ""
            )

            if not normalized_error:
                raise TechnicalAssignmentError(
                    "Для FAILED ТЗ необходимо описание ошибки.",
                )

        indexed_at = (
            changed_at
            if (target_status is TechnicalAssignmentIndexStatus.READY)
            else None
        )

        return replace(
            self,
            index_status=target_status,
            index_error=normalized_error,
            indexed_at=indexed_at,
            updated_at=changed_at,
        )
