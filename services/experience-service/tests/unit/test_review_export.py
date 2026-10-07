# services/experience-service/tests/unit/test_review_export.py

"""Правила утверждённого PDF: отрицательные примеры остаются только в Review/Experience."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_experience_service.application.use_cases.export_review import ExportReview
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    Rectangle,
    ReviewConflictError,
    ReviewNotReadyError,
    ReviewSession,
)
from pdrd_experience_service.domain.review_export import AreaStatus, export_manifest

NOW = datetime.now(UTC)
BOX = Rectangle(10.5, 20.5, 200.5, 100.5)
CARD = Rectangle(400, 50, 900, 200)


def approved():
    """Открывает и утверждает VLM с областью, текстовое VLM, rejected и Gold."""
    review = ReviewSession.open(
        job_id=UUID(int=1),
        document_id=UUID(int=2),
        source_filename="План.pdf",
        source_sha256="a" * 64,
        actor="engineer:1",
        at=NOW,
        rendered_pages=(1,),
        originals=tuple(
            OriginalFinding(f"vlm:{i}", 1, f"Полный текст {i}") for i in range(1, 4)
        ),
    )
    review = review.add_manual(
        finding_id="manual:00000000-0000-4000-8000-000000000001",
        page_number=1,
        text="Gold полный текст",
        normative_basis="СП 1",
        issue_box=BOX,
        callout_box=CARD,
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )
    for row in review.findings:
        review = review.decide(
            finding_id=row.finding_id,
            decision=Decision.REJECTED
            if row.finding_id == "vlm:3"
            else Decision.ACCEPTED,
            reason_category="false_positive" if row.finding_id == "vlm:3" else None,
            actor="engineer:1",
            at=NOW,
            expected_revision=review.revision,
        )
    return review.approve(actor="engineer:1", at=NOW, expected_revision=review.revision)


def test_only_accepted_findings_and_confirmed_regions_are_exported():
    """Rejected исчезает из обоих представлений, unlocated сохраняется только текстом."""
    manifest = export_manifest(approved(), (AreaStatus("vlm:1", 1, True, (BOX,)),))
    rows = manifest["findings"]
    assert [row["finding_id"] for row in rows] == [
        "vlm:1",
        "vlm:2",
        "manual:00000000-0000-4000-8000-000000000001",
    ]
    assert [row["number"] for row in rows] == [1, 2, 3]
    assert rows[0]["regions"][0]["x_min"] == 10.5
    assert rows[1]["regions"] == []
    assert rows[2]["experience_tag"] == "gold"
    assert rows[2]["callout_box"]["x_max"] == 900


def test_confirmation_revocation_changes_pdf_digest_without_review_revision():
    """Кеш не возвращает прежний PDF после отзыва или повторной проверки области."""
    review = approved()
    first = export_manifest(review, (AreaStatus("vlm:1", 1, True, (BOX,)),))
    revoked = export_manifest(review, (AreaStatus("vlm:1", 2, False, ()),))
    renewed = export_manifest(review, (AreaStatus("vlm:1", 3, True, (BOX,)),))
    assert first["revision"] == revoked["revision"] == renewed["revision"]
    assert len({item["digest"] for item in (first, revoked, renewed)}) == 3
    assert revoked["findings"][0]["regions"] == []


@pytest.mark.parametrize("pending", [True, False])
def test_unapproved_or_pending_review_is_not_exported(pending):
    """Ни отсутствие решений, ни отсутствие утверждения не дают PDF."""
    review = replace(approved(), approved_revision=None)
    if pending:
        review = review.reset_decision(
            finding_id="vlm:1",
            actor="engineer:1",
            at=NOW,
            expected_revision=review.revision,
        )
    with pytest.raises(ReviewNotReadyError):
        export_manifest(review, ())


async def test_review_change_between_snapshot_and_areas_is_rejected():
    """Проекция не смешивает утверждённый текст с областями другой редакции."""
    review = approved()

    class Reviews:
        """Имитирует параллельную правку после чтения первого снимка."""

        reads = 0

        async def load(self, job_id):
            """Второе чтение уже видит новую редакцию."""
            self.reads += 1
            return (
                review
                if self.reads == 1
                else replace(
                    review, revision=review.revision + 1, approved_revision=None
                )
            )

    class Areas:
        """Не возвращает неподтверждённые области."""

        async def load_status(self, *, review):
            """Возвращает пустой набор для текстового экспорта."""
            return ()

    with pytest.raises(ReviewConflictError):
        await ExportReview(Reviews(), Areas()).execute(job_id=review.job_id)


def test_full_primary_d_evidence_survives_operational_region_limit_and_revocation():
    """Шесть доказательств основной страницы сохраняются при лимите четырёх редактируемых рамок."""
    from pdrd_experience_service.domain.area_confirmation import content_signature
    from pdrd_experience_service.domain.review import (
        ProposedRegion,
        ReviewDocumentSource,
        ReviewEvidenceLocation,
    )
    from pdrd_experience_service.infrastructure.database.codec import (
        snapshot_from_json,
        snapshot_to_json,
    )

    review = approved()
    boxes = tuple(Rectangle(100 + i * 60, 100, 140 + i * 60, 140) for i in range(6))
    proofs = tuple(ProposedRegion(box, "vlm", 0.9, "document_context") for box in boxes)
    row = replace(
        review.findings[0],
        proposed_regions=proofs[:4],
        document_context_basis_sources=tuple(
            ReviewDocumentSource(
                f"D-p0001-f{i + 1:04d}",
                1,
                fact_id=f"p0001-f{i + 1:04d}",
                visual_regions=(proof,),
            )
            for i, proof in enumerate(proofs)
        ),
        evidence_locations=tuple(
            ReviewEvidenceLocation(
                1, f"D-p0001-f{i + 1:04d}", "Доказательство", (proof,)
            )
            for i, proof in enumerate(proofs)
        ),
        source_kinds=("D",),
    )
    review = replace(review, findings=(row,))
    restored = snapshot_from_json(snapshot_to_json(review), review.history)
    assert restored.findings[0].evidence_locations == row.evidence_locations
    assert content_signature(restored, restored.findings[0]) == content_signature(
        review, row
    )
    manifest = export_manifest(
        restored, (AreaStatus(row.finding_id, 1, True, boxes[:4]),)
    )
    assert len(manifest["findings"]) == 1
    assert len(manifest["findings"][0]["regions"]) == 6
    revoked = export_manifest(restored, (AreaStatus(row.finding_id, 2, False, ()),))
    assert revoked["findings"][0]["regions"] == []
