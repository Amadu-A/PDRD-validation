# services/experience-service/tests/integration/test_review_export_database.py

"""Читает подтверждения до утверждения и проверяет PDF-проекцию с настоящим PostgreSQL."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.application.use_cases.export_review import ExportReview
from pdrd_experience_service.domain.area_confirmation import ConfirmationMode

from .test_confirmed_areas_database import (
    BOX,
    adapters,
    cleanup,
    initial,
    seal,
)
from .test_confirmed_areas_database import (
    engine as engine,
)

pytestmark = pytest.mark.database


async def test_confirmation_status_and_pdf_projection_survive_postgresql(engine):
    """Подтверждение читается до решения; отзыв меняет проекцию без новой ревизии Review."""
    job = uuid4()
    reviews, areas = adapters(engine)
    review = initial(job)
    try:
        await reviews.insert(review)
        assert await areas.load_status(review=review) == ()
        await ConfirmArea(reviews, areas).execute(
            job_id=job,
            finding_id="vlm:1",
            regions=(BOX,),
            mode=ConfirmationMode.PROPOSED,
            note="",
            actor="integration:2",
            expected_review_revision=0,
            expected_confirmation_revision=0,
        )
        status = (await areas.load_status(review=review))[0]
        assert status.valid and status.revision == 1 and status.regions == (BOX,)
        approved = await seal(reviews, review)
        before = await ExportReview(reviews, areas).execute(job_id=job)
        assert before["findings"][0]["regions"]
        await RevokeArea(reviews, areas).execute(
            job_id=job,
            finding_id="vlm:1",
            actor="integration:2",
            reason="Область ошибочна",
            expected_review_revision=approved.revision,
            expected_confirmation_revision=1,
        )
        after = await ExportReview(reviews, areas).execute(job_id=job)
        assert after["revision"] == before["revision"]
        assert after["digest"] != before["digest"]
        assert after["findings"][0]["regions"] == []
        assert after["findings"][0]["text"] == "Замечание VLM"
        status = (await areas.load_status(review=approved))[0]
        assert not status.valid and status.revision == 2
    finally:
        await cleanup(engine, job)


async def test_old_confirmation_version_remains_visible_after_text_edit(engine):
    """Устаревшая подпись не теряет CAS-версию и не оживает при возврате старого текста."""
    job = uuid4()
    reviews, areas = adapters(engine)
    review = initial(job)
    try:
        await reviews.insert(review)
        await ConfirmArea(reviews, areas).execute(
            job_id=job,
            finding_id="vlm:1",
            regions=(BOX,),
            mode=ConfirmationMode.PROPOSED,
            note="",
            actor="integration:2",
            expected_review_revision=0,
            expected_confirmation_revision=0,
        )
        changed = review.edit(
            finding_id="vlm:1",
            text="Исправлено",
            normative_basis="СП 1",
            actor="integration:1",
            at=datetime.now(UTC),
            expected_revision=0,
        )
        await reviews.update(changed, expected_revision=0)
        status = (await areas.load_status(review=changed))[0]
        assert status.revision == 1 and not status.valid and status.regions == ()
        restored = changed.edit(
            finding_id="vlm:1",
            text=review.findings[0].text,
            normative_basis="СП 1",
            actor="integration:1",
            at=datetime.now(UTC),
            expected_revision=1,
        )
        await reviews.update(restored, expected_revision=1)
        assert not (await areas.load_status(review=restored))[0].valid
    finally:
        await cleanup(engine, job)
