# services/experience-service/tests/unit/test_area_confirmation.py

"""Проверяет бизнес-правила подтверждения и отзыв области без PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.domain.area_confirmation import (
    AreaConfirmationReceipt,
    ConfirmationMode,
    build_confirmation,
    content_signature,
)
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    ProposedRegion,
    Rectangle,
    ReviewConflictError,
    ReviewError,
    ReviewSession,
)
from pdrd_experience_service.domain.review_confirmation import (
    reviewed_area_confirmations,
)

JOB = UUID(int=101)
DOC = UUID(int=102)
NOW = datetime(2026, 9, 28, tzinfo=UTC)

BOX = Rectangle(100, 150, 250, 300)
CORRECTED = Rectangle(300, 250, 400, 375)


@pytest.mark.parametrize("decision", [Decision.PENDING, Decision.REJECTED])
def test_unaccepted_finding_does_not_confirm_current_area(decision) -> None:
    """Ожидание и крестик никогда не создают принятую область для Experience."""
    session = opened()
    if decision is not Decision.PENDING:
        session = session.decide(
            finding_id="vlm:1",
            decision=decision,
            reason_category="false_positive" if decision is Decision.REJECTED else None,
            actor="engineer:1",
            at=NOW,
            expected_revision=0,
        )
    assert reviewed_area_confirmations(review=session, actor="engineer:1", at=NOW) == ()
    with pytest.raises(ReviewError):
        confirm(session, mode=ConfirmationMode.DECISION)


def test_accept_geometry_only_confirms_current_area_without_changing_wise() -> None:
    """Растягивание рамки требует принятия, сохраняет Wise и исходные предложения VLM."""
    changed = opened().change_geometry(
        finding_id="vlm:1",
        regions=(CORRECTED,),
        callout_box=None,
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )
    assert reviewed_area_confirmations(review=changed, actor="engineer:1", at=NOW) == ()
    accepted = changed.decide(
        finding_id="vlm:1",
        decision=Decision.ACCEPTED,
        actor="engineer:1",
        at=NOW,
        expected_revision=changed.revision,
    )
    (area,) = reviewed_area_confirmations(review=accepted, actor="engineer:1", at=NOW)
    assert area.regions == (CORRECTED,)
    assert area.mode is ConfirmationMode.DECISION and area.note == ""
    assert accepted.findings[0].experience_tag == "wise"
    assert accepted.findings[0].proposed_regions[0].bbox == BOX
    with pytest.raises(ReviewError):
        confirm(accepted, mode=ConfirmationMode.DECISION, regions=(BOX,))


def test_accept_unlocated_finding_does_not_invent_confirmation() -> None:
    """Принятое замечание без координат не получает рамку соседнего замечания."""
    accepted = opened().decide(
        finding_id="vlm:2",
        decision=Decision.ACCEPTED,
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )
    assert (
        reviewed_area_confirmations(review=accepted, actor="engineer:1", at=NOW) == ()
    )


def test_geometry_and_undo_invalidate_old_confirmation_signature() -> None:
    """Старая область не используется повторно после растягивания и обратного Undo."""
    session = opened()
    initial = content_signature(session, session.findings[0])
    changed = session.change_geometry(
        finding_id="vlm:1",
        regions=(CORRECTED,),
        callout_box=None,
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )
    assert content_signature(changed, changed.findings[0]) != initial
    restored = changed.change_geometry(
        finding_id="vlm:1",
        regions=(BOX,),
        callout_box=None,
        actor="engineer:1",
        at=NOW,
        expected_revision=1,
    )
    assert content_signature(restored, restored.findings[0]) != initial


def opened() -> ReviewSession:
    """Создаёт серверный Review с VLM-подсказкой и текстовым замечанием."""
    return ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="drawing.pdf",
        source_sha256="a" * 64,
        originals=(
            OriginalFinding(
                "vlm:1",
                1,
                "Исходный текст",
                "СП 1",
                (
                    ProposedRegion(
                        BOX,
                        "analysis_vlm",
                        0.8,
                        "analysis_vlm",
                    ),
                ),
            ),
            OriginalFinding(
                "vlm:2",
                1,
                "Нет координат",
            ),
        ),
        rendered_pages=(1,),
        actor="engineer:1",
        at=NOW,
    )


def confirm(
    session: ReviewSession,
    *,
    finding_id: str = "vlm:1",
    regions: tuple[Rectangle, ...] = (BOX,),
    mode: ConfirmationMode = ConfirmationMode.PROPOSED,
    note: str = "",
    expected_review_revision: int | None = None,
):
    """Создаёт проверенную доменную команду с заданными координатами."""
    return build_confirmation(
        session=session,
        finding_id=finding_id,
        regions=regions,
        mode=mode,
        note=note,
        actor="engineer:2",
        at=NOW + timedelta(minutes=1),
        expected_review_revision=(
            session.revision
            if expected_review_revision is None
            else expected_review_revision
        ),
    )


def test_explicit_confirmation_keeps_vlm_proposal_separate() -> None:
    """Геометрия подтверждена, но решение finding остаётся pending."""
    session = opened()
    area = confirm(session)

    assert area.regions == (BOX,)
    assert area.content_signature == content_signature(
        session,
        session.findings[0],
    )
    assert session.findings[0].decision is Decision.PENDING
    assert session.findings[0].issue_box is None


def test_redrawn_region_requires_a_reason_but_not_vlm_proposal() -> None:
    """Инженер может исправить неверную локализацию или нарисовать отсутствующую."""
    with pytest.raises(ReviewError, match="причину"):
        confirm(
            opened(),
            regions=(CORRECTED,),
            mode=ConfirmationMode.REDRAWN,
        )

    assert confirm(
        opened(),
        finding_id="vlm:2",
        regions=(CORRECTED,),
        mode=ConfirmationMode.REDRAWN,
        note="Рамка отсутствовала",
    ).regions == (CORRECTED,)


def test_proposed_mode_never_accepts_invented_or_duplicate_regions() -> None:
    """Кнопка подтверждения VLM не может подменить произвольную геометрию."""
    for value in (
        (CORRECTED,),
        (BOX, BOX),
        (),
        (Rectangle(True, 1, 2, 3),),
    ):
        with pytest.raises(ReviewError):
            confirm(
                opened(),
                regions=value,
            )


def test_content_signature_survives_decision_but_not_edits() -> None:
    """Смена решения не ломает рамку; изменение и отмена текста ломают."""
    first = opened()

    signature = content_signature(
        first,
        first.findings[0],
    )

    decided = first.decide(
        finding_id="vlm:1",
        decision=Decision.ACCEPTED,
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )

    assert (
        content_signature(
            decided,
            decided.findings[0],
        )
        == signature
    )

    edited = decided.edit(
        finding_id="vlm:1",
        text="Исправленный текст",
        normative_basis="СП 1",
        actor="engineer:1",
        at=NOW,
        expected_revision=1,
    )

    assert (
        content_signature(
            edited,
            edited.findings[0],
        )
        != signature
    )

    restored = edited.edit(
        finding_id="vlm:1",
        text="Исходный текст",
        normative_basis="СП 1",
        actor="engineer:1",
        at=NOW,
        expected_revision=2,
    )

    assert (
        content_signature(
            restored,
            restored.findings[0],
        )
        != signature
    )

    assert content_signature(
        restored,
        restored.findings[0],
    ) != content_signature(
        edited,
        edited.findings[0],
    )


def test_stale_review_or_manual_finding_cannot_be_confirmed() -> None:
    """Не разрешаем подтверждать Gold и устаревшие версии Review."""
    with pytest.raises(ReviewConflictError):
        confirm(
            opened(),
            expected_review_revision=1,
        )

    with pytest.raises(ReviewError):
        confirm(
            opened(),
            finding_id="manual:untrusted",
        )


class FakeReviews:
    """Имитирует исходный Review без подключений к PostgreSQL."""

    def __init__(
        self,
        session: ReviewSession | None,
    ) -> None:
        """Создаёт хранилище одного задания."""
        self.session = session

    async def load(
        self,
        job_id: UUID,
    ) -> ReviewSession | None:
        """Возвращает существующий Review или None."""
        if self.session is not None and self.session.job_id == job_id:
            return self.session

        return None


class FakeWriter:
    """Наблюдает команды на порте записи подтверждений."""

    def __init__(self) -> None:
        """Создаёт пустой журнал обращений."""
        self.saved = []
        self.revoked = []

    async def save(
        self,
        *,
        confirmation,
        expected_confirmation_revision,
    ):
        """Сохраняет команду в памяти, не имитируя CAS базы."""
        self.saved.append((confirmation, expected_confirmation_revision))

        return AreaConfirmationReceipt(
            JOB,
            "vlm:1",
            1,
            True,
        )

    async def revoke(
        self,
        **kwargs,
    ):
        """Запоминает отзыв."""
        self.revoked.append(kwargs)

        return AreaConfirmationReceipt(
            JOB,
            "vlm:1",
            2,
            False,
        )


@pytest.mark.asyncio
async def test_application_confirms_without_changing_review() -> None:
    """Подтверждение не выполняет decide и не утверждает Review."""
    reviews = FakeReviews(opened())
    writer = FakeWriter()

    result = await ConfirmArea(
        reviews=reviews,
        areas=writer,
    ).execute(
        job_id=JOB,
        finding_id="vlm:1",
        regions=(BOX,),
        mode=ConfirmationMode.PROPOSED,
        note="",
        actor="engineer:1",
        expected_review_revision=0,
        expected_confirmation_revision=0,
    )

    assert result.active
    assert writer.saved[0][0].regions == (BOX,)
    assert reviews.session.revision == 0
    assert reviews.session.pending_count == 2


@pytest.mark.asyncio
async def test_application_revocation_requires_reason_and_current_review() -> None:
    """Отзыв никогда не отправляется в БД без проверки задания и причины."""
    writer = FakeWriter()

    action = RevokeArea(
        reviews=FakeReviews(opened()),
        areas=writer,
    )

    with pytest.raises(ReviewError):
        await action.execute(
            job_id=JOB,
            finding_id="vlm:1",
            actor="engineer:1",
            reason="",
            expected_review_revision=0,
            expected_confirmation_revision=1,
        )

    assert writer.revoked == []

    result = await action.execute(
        job_id=JOB,
        finding_id="vlm:1",
        actor="engineer:1",
        reason="Область неправильная",
        expected_review_revision=0,
        expected_confirmation_revision=1,
    )

    assert not result.active
    assert writer.revoked[0]["reason"] == "Область неправильная"
