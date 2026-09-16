# services/api-gateway/tests/unit/test_n8n_payload.py

"""Unit tests multipart payload для n8n orchestration."""

from uuid import uuid4

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)
from pdrd_api_gateway.domain.technical_assignment import (
    TechnicalAssignmentSnapshot,
)
from pdrd_api_gateway.infrastructure.orchestration.n8n import (
    N8nAnalysisOrchestrator,
)


def _submission() -> AnalysisSubmission:
    """Создаёт минимальную PDF analysis submission."""
    return AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="19",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
    )


def test_build_data_passes_separate_technical_assignment_document_identity() -> None:
    """n8n получает prepared identity ТЗ отдельно от analysis document_id."""
    section_id = uuid4()

    technical_assignment_document_id = uuid4()

    technical_assignment = TechnicalAssignmentSnapshot.create(
        analysis_document_id=technical_assignment_document_id,
        section_id=section_id,
        source_file="technical-assignment.docx",
        content=b"technical assignment",
    )

    snapshot = NormativeAnalysisSnapshot.create(
        section_id=section_id,
        document_ids=(),
        system_prompt="",
        technical_assignment=technical_assignment,
    )

    submission = _submission()

    artifacts = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=b"%PDF-test",
        cad_content=None,
        normative_snapshot=snapshot,
    )

    data = N8nAnalysisOrchestrator._build_data(
        artifacts,
    )

    assert data["technical_assignment_id"] == str(
        technical_assignment.technical_assignment_id,
    )

    assert data["technical_assignment_analysis_document_id"] == str(
        technical_assignment_document_id,
    )

    assert data["technical_assignment_analysis_document_id"] != data["document_id"]


def test_build_data_omits_technical_assignment_fields_without_snapshot() -> None:
    """Без ТЗ orchestration не создаёт T-specific form fields."""
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=uuid4(),
        document_ids=(),
        system_prompt="",
    )

    artifacts = AnalysisRequestArtifacts(
        submission=_submission(),
        pdf_content=b"%PDF-test",
        cad_content=None,
        normative_snapshot=snapshot,
    )

    data = N8nAnalysisOrchestrator._build_data(
        artifacts,
    )

    assert "technical_assignment_id" not in data

    assert "technical_assignment_analysis_document_id" not in data
