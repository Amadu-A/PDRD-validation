# services/api-gateway/src/pdrd_api_gateway/domain/normative_snapshot.py

"""Immutable snapshot нормативной конфигурации analysis job."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID


class InvalidNormativeAnalysisSnapshotError(ValueError):
    """Некорректный immutable snapshot нормативного анализа."""


@dataclass(frozen=True, slots=True)
class NormativeAnalysisSnapshot:
    """Фиксирует нормативный scope и active prompt в момент старта job."""

    section_id: UUID

    document_ids: tuple[
        UUID,
        ...,
    ]

    system_prompt: str

    def __post_init__(
        self,
    ) -> None:
        """Проверяет внутреннюю согласованность snapshot."""
        if len(
            set(
                self.document_ids,
            )
        ) != len(
            self.document_ids,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot не должен содержать duplicate document IDs.",
            )

        if not isinstance(
            self.system_prompt,
            str,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative system prompt должен быть строкой.",
            )

        if "\x00" in self.system_prompt:
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative system prompt содержит NUL-символ.",
            )

    @classmethod
    def create(
        cls,
        *,
        section_id: UUID,
        document_ids: tuple[
            UUID,
            ...,
        ],
        system_prompt: str,
    ) -> "NormativeAnalysisSnapshot":
        """Создаёт snapshot с ordered deduplication document IDs."""
        normalized_document_ids: list[UUID] = []

        seen: set[UUID] = set()

        for document_id in document_ids:
            if document_id in seen:
                continue

            seen.add(
                document_id,
            )

            normalized_document_ids.append(
                document_id,
            )

        return cls(
            section_id=section_id,
            document_ids=tuple(
                normalized_document_ids,
            ),
            system_prompt=system_prompt,
        )

    def as_payload(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает JSON-compatible representation для PostgreSQL."""
        return {
            "section_id": str(
                self.section_id,
            ),
            "document_ids": [
                str(
                    document_id,
                )
                for document_id in self.document_ids
            ],
            "system_prompt": self.system_prompt,
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[
            str,
            object,
        ],
    ) -> "NormativeAnalysisSnapshot":
        """Восстанавливает snapshot из JSONB payload."""
        section_value = payload.get(
            "section_id",
        )

        document_values = payload.get(
            "document_ids",
        )

        system_prompt = payload.get(
            "system_prompt",
        )

        if not isinstance(
            section_value,
            str,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot не содержит корректный section_id.",
            )

        if not isinstance(
            document_values,
            list,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot не содержит document_ids array.",
            )

        if not all(
            isinstance(
                document_id,
                str,
            )
            for document_id in document_values
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot содержит некорректный document_id.",
            )

        if not isinstance(
            system_prompt,
            str,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot не содержит system_prompt.",
            )

        try:
            section_id = UUID(
                section_value,
            )

            document_ids = tuple(
                UUID(
                    document_id,
                )
                for document_id in document_values
            )

        except ValueError as error:
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot содержит некорректный UUID.",
            ) from error

        return cls(
            section_id=section_id,
            document_ids=document_ids,
            system_prompt=system_prompt,
        )
