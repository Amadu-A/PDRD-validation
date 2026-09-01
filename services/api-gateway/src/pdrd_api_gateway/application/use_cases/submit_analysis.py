# services/api-gateway/src/pdrd_api_gateway/application/use_cases/submit_analysis.py

"""Use case приёма пользовательских файлов для анализа."""

from dataclasses import dataclass

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)


class EmptyAnalysisFileError(ValueError):
    """Ошибка пустого загруженного файла."""


@dataclass(frozen=True, slots=True)
class SubmitAnalysis:
    """Сохраняет исходные файлы и создаёт asynchronous job."""

    artifact_store: AnalysisArtifactStore
    create_analysis_job: CreateAnalysisJob

    async def execute(
        self,
        *,
        pdf_content: bytes | None,
        pdf_file_name: str | None,
        cad_content: bytes | None,
        cad_file_name: str | None,
        pages: str | None,
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

        submission = AnalysisSubmission.create(
            pdf_present=(pdf_content is not None),
            cad_present=(cad_content is not None),
            pages=pages,
            pdf_file_name=pdf_file_name,
            cad_file_name=cad_file_name,
        )

        await self.artifact_store.save_request(
            submission=submission,
            pdf_content=pdf_content,
            cad_content=cad_content,
        )

        try:
            return await self.create_analysis_job.execute(
                document_id=submission.document_id,
            )
        except BaseException:
            await self.artifact_store.delete_request(
                document_id=submission.document_id,
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
