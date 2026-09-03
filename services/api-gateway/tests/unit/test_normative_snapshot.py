# services/api-gateway/tests/unit/test_normative_snapshot.py

"""Unit tests immutable normative snapshot analysis job."""

from datetime import (
    UTC,
    datetime,
)
from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.application.ports.normative_catalog import (
    NormativeDocumentRecord,
    NormativeSectionRecord,
)
from pdrd_api_gateway.application.use_cases.resolve_normative_snapshot import (
    NormativeSelectionConflictError,
    ResolveNormativeSnapshot,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSourceMode,
    AnalysisSubmission,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    InvalidNormativeAnalysisSnapshotError,
    NormativeAnalysisSnapshot,
)
from pdrd_api_gateway.infrastructure.database.models import (
    AnalysisJobModel,
)
from pdrd_api_gateway.infrastructure.database.repositories import (
    SqlAlchemyAnalysisJobRepository,
)
from pdrd_api_gateway.infrastructure.orchestration.n8n import (
    N8nAnalysisOrchestrator,
)

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

DOCUMENT_A_ID = UUID(
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
)

DOCUMENT_B_ID = UUID(
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
)

ANALYSIS_DOCUMENT_ID = UUID(
    "cccccccc-cccc-cccc-cccc-cccccccccccc",
)

JOB_ID = UUID(
    "dddddddd-dddd-dddd-dddd-dddddddddddd",
)

BASE_TIME = datetime(
    2026,
    9,
    3,
    6,
    0,
    tzinfo=UTC,
)


class FakeNormativeCatalogReader:
    """Fake managed normative catalog."""

    def __init__(
        self,
        *,
        ready_b: bool = True,
        system_prompt: str = "  DB prompt\n",
    ) -> None:
        """Сохраняет controlled catalog state."""
        self._ready_b = ready_b
        self._system_prompt = system_prompt

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionRecord:
        """Возвращает test section."""
        assert section_id == SECTION_ID

        return NormativeSectionRecord(
            section_id=SECTION_ID,
            system_prompt=self._system_prompt,
        )

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentRecord,
        ...,
    ]:
        """Возвращает два managed test documents."""
        assert section_id == SECTION_ID

        return (
            NormativeDocumentRecord(
                document_id=DOCUMENT_A_ID,
                section_id=SECTION_ID,
                ready_for_analysis=True,
            ),
            NormativeDocumentRecord(
                document_id=DOCUMENT_B_ID,
                section_id=SECTION_ID,
                ready_for_analysis=self._ready_b,
            ),
        )


@pytest.mark.asyncio
async def test_resolver_snapshots_db_prompt_and_ordered_documents() -> None:
    """Resolver фиксирует DB prompt и удаляет duplicate document IDs."""
    snapshot = await ResolveNormativeSnapshot(
        catalog_reader=FakeNormativeCatalogReader(),
    ).execute(
        section_id=SECTION_ID,
        document_ids=(
            DOCUMENT_A_ID,
            DOCUMENT_B_ID,
            DOCUMENT_A_ID,
        ),
        prompt_override_enabled=False,
        prompt_override="ignored",
    )

    assert snapshot is not None

    assert snapshot.section_id == SECTION_ID

    assert snapshot.document_ids == (
        DOCUMENT_A_ID,
        DOCUMENT_B_ID,
    )

    assert snapshot.system_prompt == "  DB prompt\n"


@pytest.mark.asyncio
async def test_explicit_empty_prompt_override_is_preserved() -> None:
    """Empty working override отличается от restore system prompt."""
    snapshot = await ResolveNormativeSnapshot(
        catalog_reader=FakeNormativeCatalogReader(),
    ).execute(
        section_id=SECTION_ID,
        document_ids=(),
        prompt_override_enabled=True,
        prompt_override="",
    )

    assert snapshot is not None

    assert snapshot.document_ids == ()

    assert snapshot.system_prompt == ""


@pytest.mark.asyncio
async def test_non_ready_document_is_rejected_before_job_creation() -> None:
    """Неиндексированный document нельзя поместить в snapshot."""
    with pytest.raises(
        NormativeSelectionConflictError,
    ):
        await ResolveNormativeSnapshot(
            catalog_reader=FakeNormativeCatalogReader(
                ready_b=False,
            ),
        ).execute(
            section_id=SECTION_ID,
            document_ids=(DOCUMENT_B_ID,),
            prompt_override_enabled=False,
            prompt_override="",
        )


def test_snapshot_rejects_nul_prompt() -> None:
    """Snapshot не допускает NUL, но не strip-ит prompt."""
    with pytest.raises(
        InvalidNormativeAnalysisSnapshotError,
    ):
        NormativeAnalysisSnapshot.create(
            section_id=SECTION_ID,
            document_ids=(),
            system_prompt="before\x00after",
        )


def test_absent_snapshot_is_bound_as_sql_null() -> None:
    """Python None для snapshot должен сохраняться как SQL NULL."""
    column = AnalysisJobModel.__table__.c.normative_snapshot

    assert column.nullable is True

    assert column.type.none_as_null is True


def test_repository_restores_snapshot_from_jsonb_payload() -> None:
    """Repository восстанавливает immutable snapshot из ORM model."""
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=SECTION_ID,
        document_ids=(DOCUMENT_A_ID,),
        system_prompt="Snapshot prompt.",
    )

    model = AnalysisJobModel(
        id=JOB_ID,
        document_id=ANALYSIS_DOCUMENT_ID,
        normative_snapshot=snapshot.as_payload(),
        status="pending",
        attempt_count=0,
        error_code=None,
        error_message=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )

    job = SqlAlchemyAnalysisJobRepository._to_domain(
        model,
    )

    assert job.normative_snapshot == snapshot


def test_n8n_form_data_contains_exact_snapshot() -> None:
    """N8n adapter передаёт точные IDs и prompt из job snapshot."""
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=SECTION_ID,
        document_ids=(
            DOCUMENT_A_ID,
            DOCUMENT_B_ID,
        ),
        system_prompt="  exact prompt\nsecond line  ",
    )

    submission = AnalysisSubmission(
        document_id=ANALYSIS_DOCUMENT_ID,
        source_mode=AnalysisSourceMode.PDF_ONLY,
        pages="1",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
        use_explanatory_note=False,
        note_start_page=None,
        note_end_page=None,
    )

    artifacts = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=b"%PDF-test",
        cad_content=None,
        normative_snapshot=snapshot,
    )

    data = N8nAnalysisOrchestrator._build_data(
        artifacts,
    )

    assert data["normative_section_id"] == str(
        SECTION_ID,
    )

    assert data["normative_document_ids"] == (f'["{DOCUMENT_A_ID}","{DOCUMENT_B_ID}"]')

    assert data["normative_system_prompt"] == "  exact prompt\nsecond line  "


def test_analysis_job_keeps_frozen_snapshot_object() -> None:
    """AnalysisJob ссылается на frozen snapshot."""
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=SECTION_ID,
        document_ids=(DOCUMENT_A_ID,),
        system_prompt="Immutable prompt.",
    )

    job = AnalysisJob.create(
        document_id=ANALYSIS_DOCUMENT_ID,
        normative_snapshot=snapshot,
    )

    assert job.normative_snapshot is snapshot
