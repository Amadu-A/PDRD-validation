# services/experience-service/tests/unit/test_review_domain.py

"""Доменные правила принятого PDF и происхождения данных Experience."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pdrd_experience_service.domain.review import (
    Action,
    Decision,
    OriginalFinding,
    Rectangle,
    ReviewConflictError,
    ReviewError,
    ReviewNotReadyError,
    ReviewSession,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)
JOB = UUID(int=1)
DOC = UUID(int=2)
SHA = "a" * 64
ISSUE = Rectangle(10, 20, 200, 300)
CALLOUT = Rectangle(500, 300, 750, 480)


def opened() -> ReviewSession:
    """Создаёт два доверенных замечания, включая нелокализованное."""
    return ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="drawing.pdf",
        source_sha256=SHA,
        originals=(
            OriginalFinding(
                "vlm:1",
                1,
                "Найдено VLM",
                "СП 1",
            ),
            OriginalFinding(
                "vlm:2",
                2,
                "Замечание без координат",
            ),
        ),
        rendered_pages=(1, 2),
        actor="engineer:1",
        at=NOW,
    )


def decide(
    session: ReviewSession,
    finding_id: str,
    decision: Decision,
) -> ReviewSession:
    """Применяет решение по текущей редакции."""
    return session.decide(
        finding_id=finding_id,
        decision=decision,
        reason_category="false_positive" if decision is Decision.REJECTED else None,
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )


def manual(
    session: ReviewSession,
    page: int = 1,
) -> ReviewSession:
    """Добавляет Gold с реальной геометрией страницы."""
    return session.add_manual(
        finding_id=f"manual:{uuid4()}",
        page_number=page,
        text="VLM пропустила обозначение",
        normative_basis="СП 2, пункт 4",
        issue_box=ISSUE,
        callout_box=CALLOUT,
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )


def test_authoritative_vlm_findings_start_pending_including_unlocated() -> None:
    """Клиент не может пропустить замечание без визуальной карточки."""
    session = opened()

    assert len(session.findings) == 2
    assert session.pending_count == 2
    assert session.findings[1].page_number == 2
    assert session.findings[1].issue_box is None

    with pytest.raises(ReviewNotReadyError):
        session.approve(
            actor="engineer:1",
            at=NOW,
            expected_revision=0,
        )


def test_explicit_approval_exports_only_accepted_findings() -> None:
    """Отклонённые примеры остаются для обучения и исключаются из итогового PDF."""
    session = decide(
        opened(),
        "vlm:1",
        Decision.ACCEPTED,
    )
    session = decide(
        session,
        "vlm:2",
        Decision.REJECTED,
    )

    assert session.findings[0].experience_tag == "wise"
    assert session.findings[1].experience_tag == "bad"

    with pytest.raises(ReviewNotReadyError):
        session.accepted_for_pdf()

    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=2,
    )

    snapshot = session.accepted_for_pdf()

    assert snapshot.revision == session.revision == 3
    assert [item.finding_id for item in snapshot.findings] == ["vlm:1"]
    assert session.history[-1].action is Action.APPROVED


def test_edited_vlm_is_never_gold_and_edit_invalidates_approval() -> None:
    """Исправление сохраняет источник VLM при любом последующем решении."""
    session = decide(
        opened(),
        "vlm:1",
        Decision.ACCEPTED,
    )
    session = decide(
        session,
        "vlm:2",
        Decision.REJECTED,
    )
    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=2,
    )

    session = session.edit(
        finding_id="vlm:1",
        text="Исправленная оценка",
        normative_basis="СП 8",
        actor="engineer:2",
        at=NOW,
        expected_revision=3,
    )

    first = session.findings[0]

    assert first.experience_tag == "edited"
    assert first.original_text == "Найдено VLM"
    assert first.original_basis == "СП 1"
    assert first.decision is Decision.PENDING
    assert session.approved_revision is None
    assert session.history[-1].before is not None
    assert session.history[-1].after is not None
    assert session.history[-1].actor == "engineer:2"

    with pytest.raises(ReviewNotReadyError):
        session.accepted_for_pdf()

    session = decide(
        session,
        "vlm:1",
        Decision.ACCEPTED,
    )
    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=5,
    )

    assert session.accepted_for_pdf().findings[0].experience_tag == "edited"


def test_manual_gold_keeps_tag_and_both_rectangles_in_approved_snapshot() -> None:
    """Правка и утверждение сохраняют источник Gold и геометрию страницы."""
    session = manual(opened(), 2)
    gold = session.findings[-1]

    assert gold.experience_tag == "gold"
    assert gold.issue_box == ISSUE
    assert gold.callout_box == CALLOUT

    session = session.edit(
        finding_id=gold.finding_id,
        text="Уточнение пропуска",
        normative_basis="СП 3",
        actor="engineer:2",
        at=NOW,
        expected_revision=1,
    )

    assert session.findings[-1].experience_tag == "gold"

    session = decide(
        session,
        "vlm:1",
        Decision.REJECTED,
    )
    session = decide(
        session,
        "vlm:2",
        Decision.REJECTED,
    )
    session = decide(
        session,
        gold.finding_id,
        Decision.ACCEPTED,
    )
    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=5,
    )

    assert session.accepted_for_pdf().findings == (session.findings[-1],)


def test_manual_page_and_coordinates_must_match_verified_rendered_page() -> None:
    """Отсекает неверные номера страниц и координаты из браузера."""
    session = opened()

    with pytest.raises(ReviewError, match="rendered"):
        manual(session, 99)

    for coordinates in (
        (0, 0, 1, 1001),
        (5, 10, 3, 20),
        (float("nan"), 0, 1, 2),
    ):
        with pytest.raises(ReviewError):
            Rectangle(*coordinates)

    with pytest.raises(ReviewError, match="UUID"):
        session.add_manual(
            finding_id="manual:fake",
            page_number=1,
            text="A",
            normative_basis="",
            issue_box=ISSUE,
            callout_box=CALLOUT,
            actor="engineer:1",
            at=NOW,
            expected_revision=0,
        )


def test_optimistic_revision_blocks_stale_mutation() -> None:
    """Устаревшая вкладка не перезаписывает решения по старой редакции."""
    session = decide(
        opened(),
        "vlm:1",
        Decision.ACCEPTED,
    )

    with pytest.raises(ReviewConflictError):
        session.decide(
            finding_id="vlm:2",
            decision=Decision.REJECTED,
            reason_category="false_positive",
            actor="engineer:1",
            at=NOW,
            expected_revision=0,
        )

    assert len(session.history) == 2

    with pytest.raises(ReviewConflictError):
        manual(session).approve(
            actor="engineer:1",
            at=NOW,
            expected_revision=0,
        )


def test_edit_reversion_resets_tag_to_original_with_reapproval_required() -> None:
    """Возврат исходного текста VLM сбрасывает признак исправления."""
    session = opened().edit(
        finding_id="vlm:1",
        text="Ошибка VLM",
        normative_basis="СП 10",
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )

    assert session.findings[0].experience_tag == "edited"

    session = session.edit(
        finding_id="vlm:1",
        text="Найдено VLM",
        normative_basis="СП 1",
        actor="engineer:1",
        at=NOW,
        expected_revision=1,
    )

    assert session.findings[0].experience_tag is None

    unchanged = session.edit(
        finding_id="vlm:1",
        text="Найдено VLM",
        normative_basis="СП 1",
        actor="engineer:1",
        at=NOW,
        expected_revision=2,
    )

    assert unchanged is session
    assert session.findings[0].revision == 2


def test_empty_report_requires_explicit_approval_even_without_findings() -> None:
    """Пустой отчёт тоже требует явного утверждения для экспорта PDF."""
    session = ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="drawing.pdf",
        source_sha256=SHA,
        originals=(),
        rendered_pages=(1,),
        actor="engineer:1",
        at=NOW,
    )

    with pytest.raises(ReviewNotReadyError):
        session.accepted_for_pdf()

    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )

    assert session.accepted_for_pdf().findings == ()


def test_reject_unknown_identity_duplicate_sources_and_naive_audit_time() -> None:
    """Отсекает придуманные идентификаторы, дубликаты оригиналов и неверные данные аудита."""
    session = opened()

    with pytest.raises(ReviewError, match="Unknown"):
        decide(
            session,
            "client-invented-id",
            Decision.ACCEPTED,
        )

    with pytest.raises(ReviewError):
        ReviewSession.open(
            job_id=JOB,
            document_id=DOC,
            source_filename="drawing.pdf",
            source_sha256=SHA,
            originals=(
                OriginalFinding("vlm:1", 1, "A"),
                OriginalFinding("vlm:1", 1, "B"),
            ),
            rendered_pages=(1,),
            actor="engineer:1",
            at=NOW,
        )

    with pytest.raises(ReviewError):
        ReviewSession.open(
            job_id=JOB,
            document_id=DOC,
            source_filename="drawing.pdf",
            source_sha256=SHA,
            originals=(),
            rendered_pages=(1,),
            actor="",
            at=NOW,
        )

    with pytest.raises(ReviewError):
        ReviewSession.open(
            job_id=JOB,
            document_id=DOC,
            source_filename="drawing.pdf",
            source_sha256=SHA,
            originals=(),
            rendered_pages=(1,),
            actor="engineer:1",
            at=datetime(2026, 9, 25),
        )
