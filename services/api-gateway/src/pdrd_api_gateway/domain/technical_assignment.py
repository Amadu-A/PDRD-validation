# services/api-gateway/src/pdrd_api_gateway/domain/technical_assignment.py

"""Immutable snapshot технического задания analysis job."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import (
    UUID,
    uuid4,
)

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


class InvalidTechnicalAssignmentSnapshotError(
    ValueError,
):
    """Некорректный immutable snapshot ТЗ."""


def resolve_technical_assignment_mime_type(
    source_file: str,
) -> str:
    """Определяет MIME type ТЗ по разрешённому расширению."""
    if not isinstance(
        source_file,
        str,
    ):
        raise InvalidTechnicalAssignmentSnapshotError(
            "Имя файла ТЗ должно быть строкой.",
        )

    normalized = source_file.strip()

    if not normalized:
        raise InvalidTechnicalAssignmentSnapshotError(
            "Имя файла ТЗ не может быть пустым.",
        )

    if "\x00" in normalized or "/" in normalized or "\\" in normalized:
        raise InvalidTechnicalAssignmentSnapshotError(
            "Имя файла ТЗ содержит недопустимый путь.",
        )

    extension = Path(
        normalized,
    ).suffix.lower()

    try:
        return _SUPPORTED_MIME_BY_EXTENSION[extension]

    except KeyError as error:
        raise InvalidTechnicalAssignmentSnapshotError(
            "ТЗ поддерживает только PDF, DOC или DOCX.",
        ) from error


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentSnapshot:
    """Фиксирует исходное ТЗ конкретного analysis job."""

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str

    mime_type: str

    size_bytes: int

    sha256: str

    def __post_init__(
        self,
    ) -> None:
        """Проверяет snapshot после создания или deserialization."""
        if not isinstance(
            self.technical_assignment_id,
            UUID,
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "technical_assignment_id должен быть UUID.",
            )

        if not isinstance(
            self.analysis_document_id,
            UUID,
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "analysis_document_id должен быть UUID.",
            )

        if not isinstance(
            self.section_id,
            UUID,
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "section_id ТЗ должен быть UUID.",
            )

        expected_mime_type = resolve_technical_assignment_mime_type(
            self.source_file,
        )

        if self.mime_type != expected_mime_type:
            raise InvalidTechnicalAssignmentSnapshotError(
                "MIME type ТЗ не соответствует расширению файла.",
            )

        if (
            not isinstance(
                self.size_bytes,
                int,
            )
            or isinstance(
                self.size_bytes,
                bool,
            )
            or self.size_bytes <= 0
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "Размер ТЗ должен быть положительным.",
            )

        if not isinstance(
            self.sha256,
            str,
        ) or not _SHA256_PATTERN.fullmatch(
            self.sha256,
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "SHA-256 ТЗ должен содержать 64 lowercase hex-символа.",
            )

    @classmethod
    def create(
        cls,
        *,
        analysis_document_id: UUID,
        section_id: UUID,
        source_file: str,
        content: bytes,
    ) -> "TechnicalAssignmentSnapshot":
        """Создаёт immutable snapshot из uploaded bytes."""
        if (
            not isinstance(
                content,
                bytes,
            )
            or not content
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "ТЗ не может быть пустым.",
            )

        mime_type = resolve_technical_assignment_mime_type(
            source_file,
        )

        return cls(
            technical_assignment_id=uuid4(),
            analysis_document_id=analysis_document_id,
            section_id=section_id,
            source_file=source_file.strip(),
            mime_type=mime_type,
            size_bytes=len(
                content,
            ),
            sha256=sha256(
                content,
            ).hexdigest(),
        )

    def as_payload(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает JSON-compatible snapshot."""
        return {
            "technical_assignment_id": str(
                self.technical_assignment_id,
            ),
            "analysis_document_id": str(
                self.analysis_document_id,
            ),
            "section_id": str(
                self.section_id,
            ),
            "source_file": self.source_file,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[
            str,
            object,
        ],
    ) -> "TechnicalAssignmentSnapshot":
        """Восстанавливает snapshot из PostgreSQL JSONB."""
        technical_assignment_id = payload.get(
            "technical_assignment_id",
        )

        analysis_document_id = payload.get(
            "analysis_document_id",
        )

        section_id = payload.get(
            "section_id",
        )

        source_file = payload.get(
            "source_file",
        )

        mime_type = payload.get(
            "mime_type",
        )

        size_bytes = payload.get(
            "size_bytes",
        )

        source_sha256 = payload.get(
            "sha256",
        )

        string_values = (
            technical_assignment_id,
            analysis_document_id,
            section_id,
            source_file,
            mime_type,
            source_sha256,
        )

        if not all(
            isinstance(
                value,
                str,
            )
            for value in string_values
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "Snapshot ТЗ содержит некорректные строковые поля.",
            )

        if not isinstance(
            size_bytes,
            int,
        ) or isinstance(
            size_bytes,
            bool,
        ):
            raise InvalidTechnicalAssignmentSnapshotError(
                "Snapshot ТЗ содержит некорректный size_bytes.",
            )

        try:
            return cls(
                technical_assignment_id=UUID(
                    technical_assignment_id,
                ),
                analysis_document_id=UUID(
                    analysis_document_id,
                ),
                section_id=UUID(
                    section_id,
                ),
                source_file=source_file,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=source_sha256,
            )

        except ValueError as error:
            raise InvalidTechnicalAssignmentSnapshotError(
                "Snapshot ТЗ содержит некорректный UUID.",
            ) from error
