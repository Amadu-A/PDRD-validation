# services/api-gateway/tests/unit/test_submit_analysis_technical_assignment.py

"""Unit tests submission технического задания."""

from uuid import (
    UUID,
    uuid4,
)

import pytest
from pdrd_api_gateway.application.use_cases.submit_analysis import (
    SubmitAnalysis,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.domain.analysis_submission import (
    InvalidAnalysisSubmissionError,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)
from pdrd_api_gateway.domain.technical_assignment import (
    InvalidTechnicalAssignmentSnapshotError,
)


class ArtifactStoreStub:
    """Fake artifact storage для SubmitAnalysis."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует captured state."""
        self.saved_document_id: UUID | None = None

        self.technical_assignment_content: bytes | None = None

        self.deleted_document_id: UUID | None = None

    async def save_request(
        self,
        *,
        submission,
        pdf_content,
        cad_content,
    ) -> None:
        """Запоминает document_id request."""
        self.saved_document_id = submission.document_id

    async def save_technical_assignment(
        self,
        *,
        document_id: UUID,
        content: bytes,
    ) -> None:
        """Запоминает bytes ТЗ."""
        assert document_id == self.saved_document_id

        self.technical_assignment_content = content

    async def delete_request(
        self,
        *,
        document_id: UUID,
    ) -> None:
        """Запоминает compensating cleanup."""
        self.deleted_document_id = document_id


class SnapshotResolverStub:
    """Fake resolver managed selection."""

    async def execute(
        self,
        *,
        section_id,
        document_ids,
        user_package_document_ids,
        prompt_override_enabled,
        prompt_override,
    ) -> NormativeAnalysisSnapshot:
        """Возвращает валидный managed snapshot."""
        assert section_id is not None

        return NormativeAnalysisSnapshot.create(
            section_id=section_id,
            document_ids=(document_ids or ()),
            user_package_document_ids=(user_package_document_ids or ()),
            system_prompt=(
                prompt_override if prompt_override_enabled else "system prompt"
            ),
        )


class CreateAnalysisJobStub:
    """Fake durable job creation."""

    async def execute(
        self,
        *,
        document_id: UUID,
        normative_snapshot: (NormativeAnalysisSnapshot | None) = None,
    ) -> AnalysisJob:
        """Возвращает domain job."""
        return AnalysisJob.create(
            document_id=document_id,
            normative_snapshot=(normative_snapshot),
        )


def build_use_case(
    artifact_store: ArtifactStoreStub,
) -> SubmitAnalysis:
    """Создаёт SubmitAnalysis с test doubles."""
    return SubmitAnalysis(
        artifact_store=artifact_store,  # type: ignore[arg-type]
        create_analysis_job=(  # type: ignore[arg-type]
            CreateAnalysisJobStub()
        ),
        resolve_normative_snapshot=(  # type: ignore[arg-type]
            SnapshotResolverStub()
        ),
    )


@pytest.mark.asyncio
async def test_submit_analysis_attaches_technical_assignment_snapshot() -> None:
    """ТЗ сохраняется и фиксируется в immutable job snapshot."""
    artifact_store = ArtifactStoreStub()

    use_case = build_use_case(
        artifact_store,
    )

    section_id = uuid4()

    content = b"technical-assignment"

    job = await use_case.execute(
        pdf_content=b"pdf",
        pdf_file_name="drawing.pdf",
        cad_content=None,
        cad_file_name=None,
        pages="1",
        normative_section_id=section_id,
        normative_document_ids=(uuid4(),),
        technical_assignment_content=content,
        technical_assignment_file_name="ТЗ.pdf",
    )

    snapshot = job.normative_snapshot

    assert snapshot is not None

    technical_assignment = snapshot.technical_assignment

    assert technical_assignment is not None

    assert technical_assignment.section_id == section_id

    assert technical_assignment.analysis_document_id == job.document_id

    assert technical_assignment.source_file == "ТЗ.pdf"

    assert artifact_store.technical_assignment_content == content


@pytest.mark.asyncio
async def test_submit_analysis_requires_section_for_technical_assignment() -> None:
    """ТЗ без выбранного раздела отклоняется до записи artifacts."""
    artifact_store = ArtifactStoreStub()

    use_case = build_use_case(
        artifact_store,
    )

    with pytest.raises(
        InvalidAnalysisSubmissionError,
        match="выбрать нормативный раздел",
    ):
        await use_case.execute(
            pdf_content=b"pdf",
            pdf_file_name="drawing.pdf",
            cad_content=None,
            cad_file_name=None,
            pages="1",
            technical_assignment_content=b"tz",
            technical_assignment_file_name="ТЗ.pdf",
        )

    assert artifact_store.saved_document_id is None


@pytest.mark.asyncio
async def test_submit_analysis_rejects_invalid_technical_assignment_format() -> None:
    """Unsupported extension не записывается в artifact storage."""
    artifact_store = ArtifactStoreStub()

    use_case = build_use_case(
        artifact_store,
    )

    with pytest.raises(
        InvalidTechnicalAssignmentSnapshotError,
        match="PDF, DOC или DOCX",
    ):
        await use_case.execute(
            pdf_content=b"pdf",
            pdf_file_name="drawing.pdf",
            cad_content=None,
            cad_file_name=None,
            pages="1",
            normative_section_id=uuid4(),
            normative_document_ids=(uuid4(),),
            technical_assignment_content=b"tz",
            technical_assignment_file_name="ТЗ.txt",
        )

    assert artifact_store.saved_document_id is None
