# services/analysis-service/tests/unit/test_document_provenance.py

"""Регрессии типизированного происхождения D от обнаружения до финализации."""

from dataclasses import asdict, replace

import pytest
from pdrd_analysis_service.application.json_schemas import build_normative_check_schema
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentPage,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.domain.analysis import DocumentContextSource, NormativeSource
from pdrd_analysis_service.transport.http.routes import (
    _final_finding_payload,
    _finding_draft_payload,
)
from pdrd_analysis_service.transport.http.schemas import FindingDraftPayload

from .test_document_context import _ConfirmingVision, _fact, _options
from .test_finalization_lossless import PartialFinalizationModel
from .test_normative_adaptive_discovery import (
    SequentialVisionModel,
    _candidate,
    _page_facts,
    _use_case,
)


async def test_cross_page_d_survives_transport_finalization_and_normative_enrichment():
    """Одно замечание страниц 7 и 10 сохраняет D и сочетает его с N."""
    pages = (
        DocumentPage(7, "", "", (_fact("-37"),)),
        DocumentPage(10, "", "", (_fact("-35"),)),
    )
    (draft,) = await CheckCrossPageConsistency(_ConfirmingVision(), _options()).execute(
        pages=pages
    )
    assert draft.source_kinds == ("D",)
    assert [source.page for source in draft.document_context_basis_sources] == [7, 10]
    assert draft.document_context_source_ids == ("D-p0007-f0001", "D-p0010-f0001")
    restored = FindingDraftPayload.model_validate(
        _finding_draft_payload(draft).model_dump()
    ).to_domain()
    assert restored == draft
    normative = NormativeSource(
        "N1", "point", 0.9, "СП", None, 1, 0, "Применимое требование"
    )
    restored = replace(
        restored, basis_sources=(normative,), normative_source_ids=("N1",)
    )
    finalizer = FinalizeFindings(PartialFinalizationModel(), 1800, 8, 1000, 0.5)
    _, findings, _ = await finalizer.execute(
        findings=(restored,), experience_by_finding={}
    )
    (final,) = findings
    assert final.source_kinds == ("D", "N")
    assert final.document_context_basis_sources == draft.document_context_basis_sources
    assert final.evidence_locations == draft.evidence_locations
    serialized = _final_finding_payload(final).model_dump()
    assert serialized["source_kinds"] == ["D", "N"]
    assert serialized["document_context_source_ids"] == list(
        draft.document_context_source_ids
    )


async def test_page_check_rejects_fake_d_id_and_saves_selected_source():
    """Даже модель, нарушившая enum, не может сохранить выдуманное доказательство."""
    source = DocumentContextSource(
        "D-p0010-c0", 10, evidence_text="Требование проекта", chunk_index=0
    )
    candidate = _candidate(1)
    candidate["category"] = "normative_control"
    candidate["document_context_source_ids"] = [source.source_id, "D-p0099-c0"]
    model = SequentialVisionModel([{"summary": "", "violations": [candidate]}])
    _, findings, _ = await _use_case(model).execute(
        page_number=7,
        extracted_text="План",
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
        document_context_sources=(source,),
    )
    assert findings[0].document_context_source_ids == (source.source_id,)
    assert findings[0].document_context_basis_sources == (source,)
    assert findings[0].source_kinds == ("D",)
    assert findings[0].category == "other"
    ids = model.calls[0]["schema"]["properties"]["violations"]["items"]["properties"][
        "document_context_source_ids"
    ]
    assert ids["items"]["enum"] == [source.source_id]


async def test_fake_d_alone_does_not_create_document_provenance():
    """Категория и заявленные моделью типы не создают основание D."""
    candidate = _candidate(1)
    candidate.update(
        category="document_consistency",
        document_context_source_ids=["D-p0099-c0"],
        source_kinds=["D"],
    )
    model = SequentialVisionModel([{"summary": "", "violations": [candidate]}])
    _, findings, _ = await _use_case(model).execute(
        page_number=7,
        extracted_text="План",
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )
    assert findings[0].source_kinds == ()
    payload = _finding_draft_payload(findings[0]).model_dump()
    payload["source_kinds"] = ["D", "N"]
    assert FindingDraftPayload.model_validate(payload).source_kinds == []


def test_empty_d_schema_and_physical_id_invariant():
    """Без D схема остаётся лёгкой; ID сохранённого D-источника содержит его страницу."""
    schema = build_normative_check_schema(source_ids=(), max_issues=2)
    finding_schema = schema["properties"]["violations"]["items"]
    assert "document_context_source_ids" not in finding_schema["properties"]
    assert "document_context_source_ids" not in finding_schema["required"]
    with pytest.raises(ValueError):
        DocumentContextSource("D1", 7)
    with pytest.raises(ValueError):
        DocumentContextSource("D-p0010-c0", 7)
    assert asdict(DocumentContextSource("D-p0007-c0", 7))["source_id"] != "D-p0010-c0"


async def test_cross_page_merge_keeps_one_finding_and_all_source_kinds():
    """Повтор противоречия на двух страницах объединяется с независимым N-основанием."""
    from pdrd_analysis_service.application.use_cases.document_context import (
        merge_cross_page_findings,
    )

    pages = (
        DocumentPage(7, "", "", (_fact("-37"),)),
        DocumentPage(10, "", "", (_fact("-35"),)),
    )
    (cross,) = await CheckCrossPageConsistency(_ConfirmingVision(), _options()).execute(
        pages=pages
    )
    normative = NormativeSource(
        "N1", "point", 0.9, "СП", None, 1, 0, "Применимое требование"
    )
    duplicate = replace(
        cross,
        finding_id="p7-f1",
        document_context_source_ids=("D-p0010-f0001",),
        document_context_basis_sources=(
            replace(
                cross.document_context_basis_sources[1], match_type="exact_identifier"
            ),
        ),
        basis_sources=(normative,),
        normative_source_ids=("N1",),
        basis="СП",
    )
    merged = merge_cross_page_findings(
        page_findings={
            7: (duplicate,),
            10: (replace(duplicate, page=10, finding_id="p10-f1"),),
        },
        cross_findings=(cross,),
    )
    (finding,) = merged[7]
    assert merged[10] == ()
    assert finding.finding_id == cross.finding_id and finding.source_kinds == ("D", "N")
    assert finding.document_context_source_ids == cross.document_context_source_ids
    assert (
        finding.document_context_basis_sources == cross.document_context_basis_sources
    )
    assert finding.evidence_locations == cross.evidence_locations
