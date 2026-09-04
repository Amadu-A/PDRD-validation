# services/api-gateway/src/pdrd_api_gateway/application/use_cases/submit_analysis.py

"""Use case приёма пользовательских файлов для анализа."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.resolve_normative_snapshot import (
    ResolveNormativeSnapshot,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
    InvalidAnalysisSubmissionError,
)
from pdrd_api_gateway.domain.technical_assignment import (
    TechnicalAssignmentSnapshot,
)


class EmptyAnalysisFileError(ValueError):
    """Ошибка пустого загруженного файла."""


class NormativeSnapshotResolverNotConfiguredError(
    RuntimeError,
):
    """Managed selection передан без configured resolver."""


@dataclass(frozen=True, slots=True)
class SubmitAnalysis:
    """Сохраняет исходные файлы и создаёт asynchronous job."""

    artifact_store: AnalysisArtifactStore

    create_analysis_job: CreateAnalysisJob

    resolve_normative_snapshot: ResolveNormativeSnapshot | None = None

    async def execute(
        self,
        *,
        pdf_content: bytes | None,
        pdf_file_name: str | None,
        cad_content: bytes | None,
        cad_file_name: str | None,
        pages: str | None,
        use_explanatory_note: bool = False,
        note_start_page: str | int | None = None,
        note_end_page: str | int | None = None,
        normative_section_id: UUID | None = None,
        normative_document_ids: tuple[
            UUID,
            ...,
        ]
        | None = None,
        user_package_document_ids: tuple[
            UUID,
            ...,
        ]
        | None = None,
        normative_prompt_override_enabled: bool = False,
        normative_prompt_override: str = "",
        technical_assignment_content: (bytes | None) = None,
        technical_assignment_file_name: (str | None) = None,
    ) -> AnalysisJob:
        """Принимает документы и создаёт надёжное задание."""
        self._validate_file_content(
            content=pdf_content,
            file_kind="PDF",
        )

        self._validate_file_content(
            content=cad_content,
            file_kind="CAD",
        )

        self._validate_file_content(
            content=technical_assignment_content,
            file_kind="ТЗ",
        )

        if technical_assignment_content is not None and normative_section_id is None:
            raise InvalidAnalysisSubmissionError(
                "Для использования ТЗ необходимо выбрать нормативный раздел.",
            )

        normative_snapshot = None

        managed_selection_requested = (
            normative_section_id is not None
            or normative_document_ids is not None
            or user_package_document_ids is not None
            or normative_prompt_override_enabled
            or technical_assignment_content is not None
        )

        if managed_selection_requested:
            resolver = self.resolve_normative_snapshot

            if resolver is None:
                raise (
                    NormativeSnapshotResolverNotConfiguredError(
                        "Normative snapshot resolver не настроен.",
                    )
                )

            normative_snapshot = await resolver.execute(
                section_id=normative_section_id,
                document_ids=(normative_document_ids),
                user_package_document_ids=(user_package_document_ids),
                prompt_override_enabled=(normative_prompt_override_enabled),
                prompt_override=(normative_prompt_override),
            )

        submission = AnalysisSubmission.create(
            pdf_present=(pdf_content is not None),
            cad_present=(cad_content is not None),
            pages=pages,
            pdf_file_name=pdf_file_name,
            cad_file_name=cad_file_name,
            use_explanatory_note=(use_explanatory_note),
            note_start_page=note_start_page,
            note_end_page=note_end_page,
        )

        if technical_assignment_content is not None:
            if normative_snapshot is None:
                raise InvalidAnalysisSubmissionError(
                    "Для ТЗ отсутствует normative snapshot.",
                )

            technical_assignment = TechnicalAssignmentSnapshot.create(
                analysis_document_id=(submission.document_id),
                section_id=(normative_snapshot.section_id),
                source_file=(technical_assignment_file_name or ""),
                content=(technical_assignment_content),
            )

            normative_snapshot = normative_snapshot.with_technical_assignment(
                technical_assignment,
            )

        await self.artifact_store.save_request(
            submission=submission,
            pdf_content=pdf_content,
            cad_content=cad_content,
        )

        try:
            if technical_assignment_content is not None:
                await self.artifact_store.save_technical_assignment(
                    document_id=(submission.document_id),
                    content=(technical_assignment_content),
                )

            return await self.create_analysis_job.execute(
                document_id=(submission.document_id),
                normative_snapshot=(normative_snapshot),
            )

        except BaseException:
            await self.artifact_store.delete_request(
                document_id=(submission.document_id),
            )

            raise

    @staticmethod
    def _validate_file_content(
        *,
        content: bytes | None,
        file_kind: str,
    ) -> None:
        """Не допускает загруженный файл нулевого размера."""
        if content is None:
            return

        if content:
            return

        raise EmptyAnalysisFileError(
            f"Загруженный {file_kind}-файл пуст.",
        )
