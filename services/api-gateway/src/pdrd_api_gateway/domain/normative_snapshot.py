# services/api-gateway/src/pdrd_api_gateway/domain/normative_snapshot.py

"""Immutable snapshot нормативной конфигурации analysis job."""

from collections.abc import Mapping
from dataclasses import (
    dataclass,
    replace,
)
from uuid import UUID

from pdrd_api_gateway.domain.technical_assignment import (
    InvalidTechnicalAssignmentSnapshotError,
    TechnicalAssignmentSnapshot,
)


class InvalidNormativeAnalysisSnapshotError(
    ValueError,
):
    """Некорректный immutable snapshot нормативного анализа."""


def _deduplicate_ids(
    values: tuple[
        UUID,
        ...,
    ],
) -> tuple[
    UUID,
    ...,
]:
    """Удаляет duplicate UUID, сохраняя исходный порядок."""
    result: list[UUID] = []

    seen: set[UUID] = set()

    for value in values:
        if value in seen:
            continue

        seen.add(
            value,
        )

        result.append(
            value,
        )

    return tuple(
        result,
    )


@dataclass(frozen=True, slots=True)
class NormativeAnalysisSnapshot:
    """Фиксирует N/U scope, ТЗ и active prompt анализа."""

    section_id: UUID

    document_ids: tuple[
        UUID,
        ...,
    ]

    system_prompt: str

    user_package_document_ids: tuple[
        UUID,
        ...,
    ] = ()

    technical_assignment: TechnicalAssignmentSnapshot | None = None

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

        if len(
            set(
                self.user_package_document_ids,
            )
        ) != len(
            self.user_package_document_ids,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot не должен содержать "
                "duplicate user-package document IDs.",
            )

        overlap = set(
            self.document_ids,
        ) & set(
            self.user_package_document_ids,
        )

        if overlap:
            raise InvalidNormativeAnalysisSnapshotError(
                "Один document ID не может одновременно быть "
                "нормативным документом и пользовательским пакетом.",
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

        technical_assignment = self.technical_assignment

        if (
            technical_assignment is not None
            and technical_assignment.section_id != self.section_id
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "ТЗ должно принадлежать тому же разделу, что и normative snapshot.",
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
        user_package_document_ids: tuple[
            UUID,
            ...,
        ] = (),
        technical_assignment: (TechnicalAssignmentSnapshot | None) = None,
    ) -> "NormativeAnalysisSnapshot":
        """Создаёт snapshot с ordered deduplication IDs."""
        return cls(
            section_id=section_id,
            document_ids=_deduplicate_ids(
                document_ids,
            ),
            system_prompt=system_prompt,
            user_package_document_ids=_deduplicate_ids(
                user_package_document_ids,
            ),
            technical_assignment=technical_assignment,
        )

    def with_technical_assignment(
        self,
        technical_assignment: TechnicalAssignmentSnapshot,
    ) -> "NormativeAnalysisSnapshot":
        """Добавляет ТЗ один раз в immutable snapshot."""
        if self.technical_assignment is not None:
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot уже содержит ТЗ.",
            )

        return replace(
            self,
            technical_assignment=technical_assignment,
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
            "user_package_document_ids": [
                str(
                    document_id,
                )
                for document_id in self.user_package_document_ids
            ],
            "system_prompt": self.system_prompt,
            "technical_assignment": (
                self.technical_assignment.as_payload()
                if self.technical_assignment is not None
                else None
            ),
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

        package_document_values = payload.get(
            "user_package_document_ids",
            [],
        )

        system_prompt = payload.get(
            "system_prompt",
        )

        technical_assignment_value = payload.get(
            "technical_assignment",
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
            package_document_values,
            list,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot содержит некорректный "
                "user_package_document_ids array.",
            )

        if not all(
            isinstance(
                document_id,
                str,
            )
            for document_id in package_document_values
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot содержит некорректный user-package document_id.",
            )

        if not isinstance(
            system_prompt,
            str,
        ):
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot не содержит system_prompt.",
            )

        technical_assignment = None

        if technical_assignment_value is not None:
            if not isinstance(
                technical_assignment_value,
                Mapping,
            ):
                raise InvalidNormativeAnalysisSnapshotError(
                    "Normative snapshot содержит некорректное ТЗ.",
                )

            try:
                technical_assignment = TechnicalAssignmentSnapshot.from_payload(
                    technical_assignment_value,
                )

            except InvalidTechnicalAssignmentSnapshotError as error:
                raise InvalidNormativeAnalysisSnapshotError(
                    "Normative snapshot содержит некорректный snapshot ТЗ.",
                ) from error

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

            user_package_document_ids = tuple(
                UUID(
                    document_id,
                )
                for document_id in package_document_values
            )

        except ValueError as error:
            raise InvalidNormativeAnalysisSnapshotError(
                "Normative snapshot содержит некорректный UUID.",
            ) from error

        return cls(
            section_id=section_id,
            document_ids=document_ids,
            system_prompt=system_prompt,
            user_package_document_ids=(user_package_document_ids),
            technical_assignment=technical_assignment,
        )
