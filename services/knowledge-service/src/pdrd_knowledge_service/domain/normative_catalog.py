# services/knowledge-service/src/pdrd_knowledge_service/domain/normative_catalog.py

"""Domain-модель управляемого каталога документов.

Модуль описывает разделы, области каталога, категории, документы
и lifecycle индексации.

Domain не зависит от PostgreSQL, SQLAlchemy, Qdrant, FastAPI,
RabbitMQ или файловой системы.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class NormativeCatalogError(ValueError):
    """Нарушение бизнес-инварианта managed catalog."""


class CatalogArea(StrEnum):
    """Логическая область документов внутри одного раздела."""

    NORMATIVE = "normative"

    USER_PACKAGE = "user_package"


class IndexingStatus(StrEnum):
    """Состояние документа относительно vector index."""

    UPLOADED = "uploaded"

    QUEUED = "queued"

    INDEXING = "indexing"

    READY = "ready"

    FAILED = "failed"

    DELETING = "deleting"


_ALLOWED_INDEXING_TRANSITIONS: dict[
    IndexingStatus,
    frozenset[IndexingStatus],
] = {
    IndexingStatus.UPLOADED: frozenset(
        {
            IndexingStatus.QUEUED,
            IndexingStatus.DELETING,
        }
    ),
    IndexingStatus.QUEUED: frozenset(
        {
            IndexingStatus.INDEXING,
            IndexingStatus.DELETING,
        }
    ),
    IndexingStatus.INDEXING: frozenset(
        {
            IndexingStatus.READY,
            IndexingStatus.FAILED,
            IndexingStatus.DELETING,
        }
    ),
    IndexingStatus.READY: frozenset(
        {
            IndexingStatus.QUEUED,
            IndexingStatus.DELETING,
        }
    ),
    IndexingStatus.FAILED: frozenset(
        {
            IndexingStatus.QUEUED,
            IndexingStatus.DELETING,
        }
    ),
    IndexingStatus.DELETING: frozenset(),
}

_SHA256_PATTERN = re.compile(
    r"^[0-9a-fA-F]{64}$",
)


def _validate_non_blank(
    value: str,
    *,
    field_name: str,
) -> None:
    """Проверяет обязательную непустую строку."""
    if (
        not isinstance(
            value,
            str,
        )
        or not value.strip()
    ):
        raise NormativeCatalogError(
            f"{field_name} не может быть пустым.",
        )


def _validate_prompt(
    value: str,
) -> None:
    """Проверяет сохраняемый system prompt раздела."""
    if not isinstance(
        value,
        str,
    ):
        raise NormativeCatalogError(
            "System prompt раздела должен быть строкой.",
        )

    if "\x00" in value:
        raise NormativeCatalogError(
            "System prompt раздела содержит недопустимый NUL-символ.",
        )


def _validate_aware_datetime(
    value: datetime,
    *,
    field_name: str,
) -> None:
    """Требует timezone-aware datetime."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise NormativeCatalogError(
            f"{field_name} должен содержать timezone.",
        )


def _validate_entity_timestamps(
    *,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    """Проверяет временные границы domain entity."""
    _validate_aware_datetime(
        created_at,
        field_name="created_at",
    )

    _validate_aware_datetime(
        updated_at,
        field_name="updated_at",
    )

    if updated_at < created_at:
        raise NormativeCatalogError(
            "updated_at не может быть раньше created_at.",
        )


def _validate_change_time(
    *,
    changed_at: datetime,
    current_updated_at: datetime,
) -> None:
    """Не позволяет domain entity перемещаться назад во времени."""
    _validate_aware_datetime(
        changed_at,
        field_name="changed_at",
    )

    if changed_at < current_updated_at:
        raise NormativeCatalogError(
            "changed_at не может быть раньше текущего updated_at.",
        )


@dataclass(frozen=True, slots=True)
class NormativeSection:
    """Раздел managed catalog со своим системным prompt."""

    section_id: UUID

    name: str

    system_prompt: str

    created_at: datetime

    updated_at: datetime

    def __post_init__(
        self,
    ) -> None:
        """Проверяет инварианты раздела."""
        _validate_non_blank(
            self.name,
            field_name="Название раздела",
        )

        _validate_prompt(
            self.system_prompt,
        )

        _validate_entity_timestamps(
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def renamed(
        self,
        *,
        name: str,
        changed_at: datetime,
    ) -> "NormativeSection":
        """Возвращает раздел с новым названием."""
        _validate_change_time(
            changed_at=changed_at,
            current_updated_at=self.updated_at,
        )

        return replace(
            self,
            name=name,
            updated_at=changed_at,
        )

    def with_system_prompt(
        self,
        *,
        system_prompt: str,
        changed_at: datetime,
    ) -> "NormativeSection":
        """Возвращает раздел с новым сохранённым system prompt."""
        _validate_change_time(
            changed_at=changed_at,
            current_updated_at=self.updated_at,
        )

        return replace(
            self,
            system_prompt=system_prompt,
            updated_at=changed_at,
        )


@dataclass(frozen=True, slots=True)
class NormativeCategory:
    """Категория документов внутри раздела и одной catalog area."""

    category_id: UUID

    section_id: UUID

    parent_id: UUID | None

    name: str

    created_at: datetime

    updated_at: datetime

    area: CatalogArea = CatalogArea.NORMATIVE

    def __post_init__(
        self,
    ) -> None:
        """Проверяет инварианты категории."""
        _validate_non_blank(
            self.name,
            field_name="Название категории",
        )

        _validate_entity_timestamps(
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

        if self.parent_id == self.category_id:
            raise NormativeCatalogError(
                "Категория не может быть родителем самой себя.",
            )

    def renamed(
        self,
        *,
        name: str,
        changed_at: datetime,
    ) -> "NormativeCategory":
        """Возвращает категорию с новым названием."""
        _validate_change_time(
            changed_at=changed_at,
            current_updated_at=self.updated_at,
        )

        return replace(
            self,
            name=name,
            updated_at=changed_at,
        )

    def moved_under(
        self,
        *,
        parent_id: UUID | None,
        changed_at: datetime,
    ) -> "NormativeCategory":
        """Возвращает категорию с новым parent category."""
        _validate_change_time(
            changed_at=changed_at,
            current_updated_at=self.updated_at,
        )

        return replace(
            self,
            parent_id=parent_id,
            updated_at=changed_at,
        )


@dataclass(frozen=True, slots=True)
class NormativeDocument:
    """Метаданные managed PDF/DOC/DOCX документа."""

    document_id: UUID

    section_id: UUID

    category_id: UUID | None

    original_name: str

    storage_key: str

    mime_type: str

    size_bytes: int

    sha256: str

    index_status: IndexingStatus

    index_error: str | None

    indexed_at: datetime | None

    created_at: datetime

    updated_at: datetime

    area: CatalogArea = CatalogArea.NORMATIVE

    def __post_init__(
        self,
    ) -> None:
        """Проверяет инварианты managed документа."""
        _validate_non_blank(
            self.original_name,
            field_name="Имя документа",
        )

        _validate_non_blank(
            self.storage_key,
            field_name="Storage key документа",
        )

        _validate_non_blank(
            self.mime_type,
            field_name="MIME type документа",
        )

        if self.size_bytes <= 0:
            raise NormativeCatalogError(
                "Размер нормативного документа должен быть больше нуля.",
            )

        if not _SHA256_PATTERN.fullmatch(
            self.sha256,
        ):
            raise NormativeCatalogError(
                "SHA-256 документа должен содержать 64 hex-символа.",
            )

        _validate_entity_timestamps(
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

        self._validate_index_state()

    @property
    def ready_for_analysis(
        self,
    ) -> bool:
        """Показывает, разрешено ли использовать документ в анализе."""
        return self.index_status is IndexingStatus.READY

    def moved_to_category(
        self,
        *,
        category_id: UUID | None,
        changed_at: datetime,
    ) -> "NormativeDocument":
        """Возвращает документ в другой категории той же области."""
        _validate_change_time(
            changed_at=changed_at,
            current_updated_at=self.updated_at,
        )

        return replace(
            self,
            category_id=category_id,
            updated_at=changed_at,
        )

    def transition_indexing(
        self,
        *,
        target_status: IndexingStatus,
        changed_at: datetime,
        error: str | None = None,
    ) -> "NormativeDocument":
        """Переводит документ в допустимое состояние indexing lifecycle."""
        _validate_change_time(
            changed_at=changed_at,
            current_updated_at=self.updated_at,
        )

        allowed = _ALLOWED_INDEXING_TRANSITIONS[self.index_status]

        if target_status not in allowed:
            raise NormativeCatalogError(
                "Недопустимый переход состояния индексации: "
                f"{self.index_status.value} -> {target_status.value}.",
            )

        if target_status is IndexingStatus.FAILED:
            if error is None:
                raise NormativeCatalogError(
                    "Для состояния failed требуется описание ошибки.",
                )

            _validate_non_blank(
                error,
                field_name="Ошибка индексации",
            )

            next_error = error

        else:
            if error is not None:
                raise NormativeCatalogError(
                    "Описание ошибки допустимо только для состояния failed.",
                )

            next_error = None

        next_indexed_at = changed_at if target_status is IndexingStatus.READY else None

        return replace(
            self,
            index_status=target_status,
            index_error=next_error,
            indexed_at=next_indexed_at,
            updated_at=changed_at,
        )

    def _validate_index_state(
        self,
    ) -> None:
        """Проверяет согласованность status, error и indexed timestamp."""
        if self.index_status is IndexingStatus.FAILED:
            if self.index_error is None:
                raise NormativeCatalogError(
                    "Для состояния failed требуется index_error.",
                )

            _validate_non_blank(
                self.index_error,
                field_name="Ошибка индексации",
            )

        elif self.index_error is not None:
            raise NormativeCatalogError(
                "index_error допустим только для состояния failed.",
            )

        if self.index_status is IndexingStatus.READY:
            if self.indexed_at is None:
                raise NormativeCatalogError(
                    "Для состояния ready требуется indexed_at.",
                )

            _validate_aware_datetime(
                self.indexed_at,
                field_name="indexed_at",
            )

            if self.indexed_at < self.created_at:
                raise NormativeCatalogError(
                    "indexed_at не может быть раньше created_at.",
                )

            if self.indexed_at > self.updated_at:
                raise NormativeCatalogError(
                    "indexed_at не может быть позже updated_at.",
                )

        elif self.indexed_at is not None:
            raise NormativeCatalogError(
                "indexed_at допустим только для состояния ready.",
            )
