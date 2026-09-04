# services/api-gateway/src/pdrd_api_gateway/application/use_cases/resolve_normative_snapshot.py

"""Use case фиксации normative/package snapshot перед созданием job."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.normative_catalog import (
    NormativeCatalogNotFoundError,
    NormativeCatalogReader,
)
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogNotFoundError as ManagedCatalogNotFoundError,
)
from pdrd_api_gateway.application.ports.user_package_catalog import (
    UserPackageCatalogManager,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)


class InvalidNormativeSelectionError(ValueError):
    """Пользователь передал некорректный managed selection."""


class NormativeSelectionConflictError(RuntimeError):
    """Выбранные документы пока нельзя использовать для анализа."""


class UserPackageReaderNotConfiguredError(RuntimeError):
    """Package selection передан без configured package reader."""


@dataclass(frozen=True, slots=True)
class ResolveNormativeSnapshot:
    """Валидирует selection и фиксирует exact active system prompt."""

    catalog_reader: NormativeCatalogReader

    user_package_reader: UserPackageCatalogManager | None = None

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
        user_package_document_ids: tuple[
            UUID,
            ...,
        ]
        | None = None,
    ) -> NormativeAnalysisSnapshot | None:
        """Возвращает immutable snapshot либо legacy None."""
        selection_absent = (
            section_id is None
            and document_ids is None
            and user_package_document_ids is None
        )

        if selection_absent:
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

        normalized_package_ids = self._normalize_document_ids(
            user_package_document_ids if user_package_document_ids is not None else (),
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
                + self._format_ids(
                    missing_ids,
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
                + self._format_ids(
                    foreign_ids,
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
                + self._format_ids(
                    unavailable_ids,
                )
                + ".",
            )

        await self._validate_user_packages(
            section_id=section_id,
            document_ids=normalized_package_ids,
        )

        active_prompt = (
            prompt_override if prompt_override_enabled else section.system_prompt
        )

        return NormativeAnalysisSnapshot.create(
            section_id=section.section_id,
            document_ids=normalized_document_ids,
            user_package_document_ids=normalized_package_ids,
            system_prompt=active_prompt,
        )

    async def _validate_user_packages(
        self,
        *,
        section_id: UUID,
        document_ids: tuple[
            UUID,
            ...,
        ],
    ) -> None:
        """Проверяет package scope независимо от нормативного scope."""
        if not document_ids:
            return

        reader = self.user_package_reader

        if reader is None:
            raise UserPackageReaderNotConfiguredError(
                "User-package catalog reader не настроен.",
            )

        try:
            documents = await reader.list_documents(
                section_id=section_id,
            )

        except ManagedCatalogNotFoundError as error:
            raise InvalidNormativeSelectionError(
                str(
                    error,
                )
            ) from error

        documents_by_id = {document.document_id: document for document in documents}

        missing_ids = [
            document_id
            for document_id in document_ids
            if document_id not in documents_by_id
        ]

        if missing_ids:
            raise InvalidNormativeSelectionError(
                "Пользовательские документы отсутствуют "
                "в выбранном разделе: "
                + self._format_ids(
                    missing_ids,
                )
                + ".",
            )

        foreign_ids = [
            document.document_id
            for document in documents
            if (
                document.document_id in document_ids
                and document.section_id != section_id
            )
        ]

        if foreign_ids:
            raise InvalidNormativeSelectionError(
                "Пользовательские документы принадлежат "
                "другому разделу: "
                + self._format_ids(
                    foreign_ids,
                )
                + ".",
            )

        unavailable_ids = [
            document_id
            for document_id in document_ids
            if not documents_by_id[document_id].ready_for_analysis
        ]

        if unavailable_ids:
            raise NormativeSelectionConflictError(
                "Пользовательские документы ещё не готовы "
                "к анализу: "
                + self._format_ids(
                    unavailable_ids,
                )
                + ".",
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

    @staticmethod
    def _format_ids(
        document_ids: list[UUID,],
    ) -> str:
        """Формирует стабильный список UUID для application error."""
        return ", ".join(
            str(
                document_id,
            )
            for document_id in document_ids
        )
