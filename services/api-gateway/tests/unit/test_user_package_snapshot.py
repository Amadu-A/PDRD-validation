# services/api-gateway/tests/unit/test_user_package_snapshot.py

"""Unit tests immutable selection пользовательских пакетов."""

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
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeDocumentView,
)
from pdrd_api_gateway.application.use_cases.resolve_normative_snapshot import (
    NormativeSelectionConflictError,
    ResolveNormativeSnapshot,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSourceMode,
    AnalysisSubmission,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)
from pdrd_api_gateway.infrastructure.orchestration.n8n import (
    N8nAnalysisOrchestrator,
)

SECTION_ID = UUID(
    "11111111-1111-1111-1111-111111111111",
)

NORMATIVE_ID = UUID(
    "22222222-2222-2222-2222-222222222222",
)

PACKAGE_A_ID = UUID(
    "33333333-3333-3333-3333-333333333333",
)

PACKAGE_B_ID = UUID(
    "44444444-4444-4444-4444-444444444444",
)

ANALYSIS_ID = UUID(
    "55555555-5555-5555-5555-555555555555",
)

NOW = datetime(
    2026,
    9,
    4,
    12,
    0,
    tzinfo=UTC,
)


class FakeNormativeReader:
    """Fake normative scope."""

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionRecord:
        """Возвращает test section."""
        assert section_id == SECTION_ID

        return NormativeSectionRecord(
            section_id=SECTION_ID,
            system_prompt="DB prompt.",
        )

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentRecord,
        ...,
    ]:
        """Возвращает один READY норматив."""
        assert section_id == SECTION_ID

        return (
            NormativeDocumentRecord(
                document_id=NORMATIVE_ID,
                section_id=SECTION_ID,
                ready_for_analysis=True,
            ),
        )


class FakeUserPackageReader:
    """Fake package scope."""

    def __init__(
        self,
        *,
        ready_b: bool = True,
    ) -> None:
        """Сохраняет состояние второго документа."""
        self._ready_b = ready_b

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentView,
        ...,
    ]:
        """Возвращает два package documents."""
        assert section_id == SECTION_ID

        return (
            self._document(
                document_id=PACKAGE_A_ID,
                ready=True,
            ),
            self._document(
                document_id=PACKAGE_B_ID,
                ready=self._ready_b,
            ),
        )

    @staticmethod
    def _document(
        *,
        document_id: UUID,
        ready: bool,
    ) -> NormativeDocumentView:
        """Строит package view."""
        return NormativeDocumentView(
            document_id=document_id,
            section_id=SECTION_ID,
            category_id=None,
            original_name=f"{document_id}.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            index_status=("ready" if ready else "indexing"),
            index_error=None,
            indexed_at=(NOW if ready else None),
            ready_for_analysis=ready,
            created_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.asyncio
async def test_resolver_snapshots_selected_user_packages() -> None:
    """Resolver дедуплицирует package IDs независимо от нормативов."""
    snapshot = await ResolveNormativeSnapshot(
        catalog_reader=FakeNormativeReader(),
        user_package_reader=FakeUserPackageReader(),
    ).execute(
        section_id=SECTION_ID,
        document_ids=(NORMATIVE_ID,),
        user_package_document_ids=(
            PACKAGE_A_ID,
            PACKAGE_B_ID,
            PACKAGE_A_ID,
        ),
        prompt_override_enabled=False,
        prompt_override="",
    )

    assert snapshot is not None

    assert snapshot.document_ids == (NORMATIVE_ID,)

    assert snapshot.user_package_document_ids == (
        PACKAGE_A_ID,
        PACKAGE_B_ID,
    )


@pytest.mark.asyncio
async def test_non_ready_package_is_rejected() -> None:
    """Неиндексированный package document не попадает в job."""
    with pytest.raises(
        NormativeSelectionConflictError,
        match="Пользовательские документы",
    ):
        await ResolveNormativeSnapshot(
            catalog_reader=FakeNormativeReader(),
            user_package_reader=FakeUserPackageReader(
                ready_b=False,
            ),
        ).execute(
            section_id=SECTION_ID,
            document_ids=(NORMATIVE_ID,),
            user_package_document_ids=(PACKAGE_B_ID,),
            prompt_override_enabled=False,
            prompt_override="",
        )


def test_old_snapshot_payload_remains_compatible() -> None:
    """Старые job JSONB без package field восстанавливаются."""
    snapshot = NormativeAnalysisSnapshot.from_payload(
        {
            "section_id": str(
                SECTION_ID,
            ),
            "document_ids": [
                str(
                    NORMATIVE_ID,
                ),
            ],
            "system_prompt": "Old job.",
        }
    )

    assert snapshot.document_ids == (NORMATIVE_ID,)

    assert snapshot.user_package_document_ids == ()


def test_snapshot_payload_keeps_package_scope_separate() -> None:
    """JSONB содержит отдельные списки нормативов и package docs."""
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=SECTION_ID,
        document_ids=(NORMATIVE_ID,),
        user_package_document_ids=(PACKAGE_A_ID,),
        system_prompt="Prompt.",
    )

    payload = snapshot.as_payload()

    assert payload["document_ids"] == [
        str(
            NORMATIVE_ID,
        ),
    ]

    assert payload["user_package_document_ids"] == [
        str(
            PACKAGE_A_ID,
        ),
    ]


def test_n8n_form_data_contains_package_snapshot() -> None:
    """Worker передаёт package selection отдельно от нормативов."""
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=SECTION_ID,
        document_ids=(NORMATIVE_ID,),
        user_package_document_ids=(
            PACKAGE_A_ID,
            PACKAGE_B_ID,
        ),
        system_prompt="Exact prompt.",
    )

    submission = AnalysisSubmission(
        document_id=ANALYSIS_ID,
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

    assert data["normative_document_ids"] == (f'["{NORMATIVE_ID}"]')

    assert data["user_package_document_ids"] == (f'["{PACKAGE_A_ID}","{PACKAGE_B_ID}"]')
