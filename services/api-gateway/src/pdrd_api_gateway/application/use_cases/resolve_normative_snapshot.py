# services/api-gateway/src/pdrd_api_gateway/application/use_cases/resolve_normative_snapshot.py

"""Use case фиксации нормативного snapshot перед созданием job."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.normative_catalog import (
    NormativeCatalogNotFoundError,
    NormativeCatalogReader,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)


class InvalidNormativeSelectionError(ValueError):
    """Пользователь передал некорректный нормативный selection."""


class NormativeSelectionConflictError(RuntimeError):
    """Выбранные документы пока нельзя использовать для анализа."""


@dataclass(frozen=True, slots=True)
class ResolveNormativeSnapshot:
    """Валидирует selection и фиксирует exact active system prompt."""

    catalog_reader: NormativeCatalogReader

    async def execute(
        self,
        *,
        section_id: UUID | None,
        document_ids: tuple[
            UUID,
            ...,
        ]
        | None,
        prompt_override_enabled: bool,
        prompt_override: str,
    ) -> NormativeAnalysisSnapshot | None:
        """Возвращает immutable snapshot либо legacy None."""
        if section_id is None and document_ids is None:
            if prompt_override_enabled:
                raise InvalidNormativeSelectionError(
                    "Prompt override нельзя использовать без нормативного раздела.",
                )

            return None

        if section_id is None or document_ids is None:
            raise InvalidNormativeSelectionError(
                "normative_section_id и normative_document_ids "
                "должны передаваться вместе.",
            )

        normalized_document_ids = self._normalize_document_ids(
            document_ids,
        )

        try:
            section = await self.catalog_reader.get_section(
                section_id=section_id,
            )

            documents = await self.catalog_reader.list_documents(
                section_id=section_id,
            )

        except NormativeCatalogNotFoundError as error:
            raise InvalidNormativeSelectionError(
                str(
                    error,
                )
            ) from error

        documents_by_id = {document.document_id: document for document in documents}

        missing_ids = [
            document_id
            for document_id in normalized_document_ids
            if document_id not in documents_by_id
        ]

        if missing_ids:
            raise InvalidNormativeSelectionError(
                "Документы отсутствуют в выбранном нормативном разделе: "
                + ", ".join(
                    str(
                        document_id,
                    )
                    for document_id in missing_ids
                )
                + ".",
            )

        foreign_ids = [
            document.document_id
            for document in documents
            if (
                document.document_id in normalized_document_ids
                and document.section_id != section_id
            )
        ]

        if foreign_ids:
            raise InvalidNormativeSelectionError(
                "Документы принадлежат другому нормативному разделу: "
                + ", ".join(
                    str(
                        document_id,
                    )
                    for document_id in foreign_ids
                )
                + ".",
            )

        unavailable_ids = [
            document_id
            for document_id in normalized_document_ids
            if not documents_by_id[document_id].ready_for_analysis
        ]

        if unavailable_ids:
            raise NormativeSelectionConflictError(
                "Документы ещё не готовы к анализу: "
                + ", ".join(
                    str(
                        document_id,
                    )
                    for document_id in unavailable_ids
                )
                + ".",
            )

        active_prompt = (
            prompt_override if prompt_override_enabled else section.system_prompt
        )

        return NormativeAnalysisSnapshot.create(
            section_id=section.section_id,
            document_ids=normalized_document_ids,
            system_prompt=active_prompt,
        )

    @staticmethod
    def _normalize_document_ids(
        document_ids: tuple[
            UUID,
            ...,
        ],
    ) -> tuple[
        UUID,
        ...,
    ]:
        """Удаляет duplicate IDs, сохраняя пользовательский порядок."""
        result: list[UUID] = []

        seen: set[UUID] = set()

        for document_id in document_ids:
            if document_id in seen:
                continue

            seen.add(
                document_id,
            )

            result.append(
                document_id,
            )

        return tuple(
            result,
        )
