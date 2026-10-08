# services/experience-service/tests/unit/test_equipment_review_roundtrip.py

"""Проверяет EQ provenance при открытии, сохранении и экспорте Review."""

from datetime import UTC, datetime
from uuid import UUID

from pdrd_experience_service.domain.review import Decision, ReviewSession
from pdrd_experience_service.domain.review_export import export_manifest
from pdrd_experience_service.infrastructure.analysis.visualization import (
    completed_analysis_from_visualization,
)
from pdrd_experience_service.infrastructure.database.codec import (
    snapshot_from_json,
    snapshot_to_json,
)

JOB = UUID(int=1)
DOCUMENT = UUID(int=2)
NOW = datetime.now(UTC)


def test_equipment_source_survives_review_decision_and_export() -> None:
    """Принятое EQ-замечание сохраняет snapshot даже после правки Review."""
    source = {
        "source_id": "EQ-1234",
        "manufacturer": "MEAN WELL",
        "model": "DRC-100B",
        "variant": "",
        "property_name": "output_voltage",
        "value_raw": "21...29",
        "unit_raw": "V",
        "page": 3,
        "snippet": "DRC-100B Output voltage 21...29 V",
        "document_revision": "B",
        "sha256": "b" * 64,
        "source_url": "https://www.meanwell.com/drc-100b.pdf",
        "trust_status": "trusted",
    }
    details = {
        "project_value": "30",
        "project_unit": "V",
        "manufacturer_value": "21...29",
        "manufacturer_unit": "V",
    }
    completed = completed_analysis_from_visualization(
        job_id=JOB,
        document_id=DOCUMENT,
        source_filename="drawing.pdf",
        pdf_content=b"%PDF-1.4 test",
        result={
            "findings": [
                {
                    "finding_id": "EQ-finding-1",
                    "page": 1,
                    "status": "confirmed",
                    "comment": "Проектное напряжение вне диапазона.",
                    "basis": "Техническая документация производителя",
                    "equipment_documentation_basis_sources": [source],
                    "equipment_details": details,
                }
            ]
        },
        visualization={
            "job_id": str(JOB),
            "document_id": str(DOCUMENT),
            "pages": [{"page_number": 1, "locations": []}],
        },
    )
    review = ReviewSession.open(
        job_id=JOB,
        document_id=DOCUMENT,
        source_filename=completed.source_filename,
        source_sha256=completed.source_sha256,
        originals=completed.findings,
        rendered_pages=completed.rendered_pages,
        actor="engineer:1",
        at=NOW,
    )
    review = review.decide(
        finding_id="EQ-finding-1",
        decision=Decision.ACCEPTED,
        actor="engineer:1",
        at=NOW,
        expected_revision=review.revision,
    )
    review = review.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=review.revision,
    )
    restored = snapshot_from_json(snapshot_to_json(review), review.history)
    exported = export_manifest(restored, ())
    row = exported["findings"][0]

    assert row["equipment_documentation_basis_sources"] == [source]
    assert row["equipment_details"] == details
    assert row["source_kinds"] == ["EQ"]
