# services/api-gateway/src/pdrd_api_gateway/application/use_cases/submit_analysis.py

"""Use case приёма пользовательских файлов для анализа."""

from dataclasses import (
    dataclass,
    replace,
)
from hashlib import sha256
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
    resolve_technical_assignment_mime_type,
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
        normative_document_ids: (
            tuple[
                UUID,
                ...,
            ]
            | None
        ) = None,
        user_package_document_ids: (
            tuple[
                UUID,
                ...,
            ]
            | None
        ) = None,
        normative_prompt_override_enabled: bool = False,
        normative_prompt_override: str = "",
        technical_assignment_content: (bytes | None) = None,
        technical_assignment_file_name: (str | None) = None,
        technical_assignment_id: UUID | None = None,
        technical_assignment_analysis_document_id: (UUID | None) = None,
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

        self._validate_prepared_technical_assignment(
            content=technical_assignment_content,
            technical_assignment_id=technical_assignment_id,
            analysis_document_id=(technical_assignment_analysis_document_id),
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

        if (
            technical_assignment_content is not None
            and technical_assignment_analysis_document_id is not None
        ):
            submission = replace(
                submission,
                document_id=(technical_assignment_analysis_document_id),
            )

        if technical_assignment_content is not None:
            if normative_snapshot is None:
                raise InvalidAnalysisSubmissionError(
                    "Для ТЗ отсутствует normative snapshot.",
                )

            technical_assignment = self._build_technical_assignment_snapshot(
                analysis_document_id=(submission.document_id),
                section_id=(normative_snapshot.section_id),
                source_file=(technical_assignment_file_name or ""),
                content=(technical_assignment_content),
                prepared_technical_assignment_id=(technical_assignment_id),
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
    def _build_technical_assignment_snapshot(
        *,
        analysis_document_id: UUID,
        section_id: UUID,
        source_file: str,
        content: bytes,
        prepared_technical_assignment_id: (UUID | None),
    ) -> TechnicalAssignmentSnapshot:
        """Переиспользует preflight identity либо создаёт новую."""
        if prepared_technical_assignment_id is None:
            return TechnicalAssignmentSnapshot.create(
                analysis_document_id=(analysis_document_id),
                section_id=section_id,
                source_file=source_file,
                content=content,
            )

        normalized_source_file = source_file.strip()

        return TechnicalAssignmentSnapshot(
            technical_assignment_id=(prepared_technical_assignment_id),
            analysis_document_id=(analysis_document_id),
            section_id=section_id,
            source_file=normalized_source_file,
            mime_type=(
                resolve_technical_assignment_mime_type(
                    normalized_source_file,
                )
            ),
            size_bytes=len(
                content,
            ),
            sha256=sha256(
                content,
            ).hexdigest(),
        )

    @staticmethod
    def _validate_prepared_technical_assignment(
        *,
        content: bytes | None,
        technical_assignment_id: UUID | None,
        analysis_document_id: UUID | None,
    ) -> None:
        """Проверяет целостность optional preflight identity."""
        identity_present = (
            technical_assignment_id is not None or analysis_document_id is not None
        )

        if identity_present and content is None:
            raise InvalidAnalysisSubmissionError(
                "Prepared identity ТЗ передан без файла ТЗ.",
            )

        if (technical_assignment_id is None) != (analysis_document_id is None):
            raise InvalidAnalysisSubmissionError(
                "Prepared identity ТЗ должен содержать "
                "technical_assignment_id и "
                "analysis_document_id одновременно.",
            )

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
