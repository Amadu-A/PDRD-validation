# services/experience-service/tests/unit/test_equipment_update.py

"""Явное EQ-дополнение сохраняет решения, версии, provenance и полный пакет аудита."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pdrd_experience_service.application.use_cases.equipment_update import (
    import_equipment_update,
)
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    ReviewConflictError,
    ReviewError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.codec import (
    event_from_json,
    event_to_json,
)


def review():
    """Создаёт исходное принятое N замечание в неутверждённом Review."""
    session = ReviewSession.open(
        job_id=uuid4(),
        document_id=uuid4(),
        source_filename="drawing.pdf",
        source_sha256="a" * 64,
        originals=(OriginalFinding("N-existing", 1, "Исходное замечание"),),
        rendered_pages=(1, 2),
        actor="engineer:1",
        at=datetime.now(UTC),
    )
    return session.decide(
        finding_id="N-existing",
        decision=Decision.ACCEPTED,
        actor="engineer:1",
        at=datetime.now(UTC),
        expected_revision=0,
    )


def finding(identifier="EQ-new", page=2):
    """Серверный кандидат содержит snapshot и страницу характеристики."""
    return {
        "finding_id": identifier,
        "page": page,
        "status": "confirmed",
        "comment": "Напряжение вне диапазона",
        "equipment_documentation_basis_sources": [
            {
                "source_id": "EQ-" + "b" * 32,
                "sha256": "c" * 64,
                "page": 3,
                "snippet": "РИП-12 исп. 51: 12 В",
            }
        ],
        "equipment_details": {"model": "РИП-12"},
        "visual_regions": [],
    }


async def test_batch_has_one_cas_revision_and_complete_audit_and_preserves_accepted():
    """Две находки добавляются одним событием; принятое N замечание не меняется."""
    current = review()
    repository = SimpleNamespace(
        load=AsyncMock(return_value=current), update=AsyncMock()
    )
    updated = await import_equipment_update(
        repository,
        job_id=current.job_id,
        actor="engineer:2",
        expected_revision=current.revision,
        equipment_revision=25,
        findings=[finding(), finding("EQ-next", 1)],
    )
    assert updated.findings[0] is current.findings[0]
    assert updated.findings[0].decision is Decision.ACCEPTED
    assert updated.revision == current.revision + 1
    assert len(updated.history) == len(current.history) + 1
    assert len(updated.history[-1].added_findings) == 2
    assert updated.pending_count == 2
    event = updated.history[-1]
    restored = event_from_json(
        action=event.action.value,
        actor=event.actor,
        occurred_at=event.occurred_at,
        revision=event.session_revision,
        details=event_to_json(event),
    )
    assert restored == event
    assert restored.equipment_revision == 25
    assert all(item.source_kinds == ("EQ",) for item in updated.findings[1:])
    assert updated.findings[1].equipment_details["equipment_report_revision"] == 25
    repository.update.assert_awaited_once_with(
        updated, expected_revision=current.revision
    )
    repository.load.return_value = updated
    replay = await import_equipment_update(
        repository,
        job_id=current.job_id,
        actor="engineer:2",
        expected_revision=updated.revision,
        equipment_revision=25,
        findings=[finding(), finding("EQ-next", 1)],
    )
    assert replay is updated
    assert repository.update.await_count == 1


@pytest.mark.parametrize("case", ["stale", "approved", "wrong_page", "no_evidence"])
async def test_invalid_update_never_writes_review(case):
    """Утверждённая редакция, старая версия и неподтверждённые находки защищены."""
    current, raw = review(), finding()
    expected = current.revision
    if case == "stale":
        expected -= 1
    if case == "approved":
        current = current.approve(
            actor="engineer:1", at=datetime.now(UTC), expected_revision=current.revision
        )
        expected = current.revision
    if case == "wrong_page":
        raw["page"] = 99
    if case == "no_evidence":
        raw["equipment_documentation_basis_sources"] = []
    repository = SimpleNamespace(
        load=AsyncMock(return_value=current), update=AsyncMock()
    )
    with pytest.raises((ReviewError, ReviewConflictError)):
        await import_equipment_update(
            repository,
            job_id=current.job_id,
            actor="engineer:2",
            expected_revision=expected,
            equipment_revision=25,
            findings=[raw],
        )
    repository.update.assert_not_awaited()
