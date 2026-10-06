# services/experience-service/tests/unit/test_experience_selection.py

"""Регрессии отбора локализованных, проверенных и явно рассмотренных данных Experience."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pdrd_experience_service.application.use_cases.select_experience import (
    SelectExperience,
)
from pdrd_experience_service.domain.experience_selection import (
    ConfirmedFindingArea,
    LearningUse,
    select_experience_candidates,
)
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    ProposedRegion,
    Rectangle,
    ReviewError,
    ReviewNotReadyError,
    ReviewSession,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)
JOB = UUID(int=1)
DOC = UUID(int=2)
REGION = Rectangle(100, 150, 230, 300)
CALLOUT = Rectangle(600, 400, 900, 650)


def opened() -> ReviewSession:
    """Создаёт доверенные замечания VLM, включая нелокализованное."""
    return ReviewSession.open(
        job_id=JOB,
        document_id=DOC,
        source_filename="drawing.pdf",
        source_sha256="f" * 64,
        originals=(
            OriginalFinding(
                "vlm:1",
                2,
                "Первый текст VLM",
                "СП 1",
                (ProposedRegion(REGION, "vlm", 0.9, "vlm"),),
            ),
            OriginalFinding(
                "vlm:2",
                2,
                "Нет определённой области",
            ),
        ),
        rendered_pages=(2,),
        actor="engineer:1",
        at=NOW,
    )


def decide(
    session: ReviewSession,
    finding_id: str,
    decision: Decision,
) -> ReviewSession:
    """Принимает решение и обновляет редакцию ревью."""
    return session.decide(
        finding_id=finding_id,
        decision=decision,
        reason_category="false_positive" if decision is Decision.REJECTED else None,
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )


def sealed(
    first: Decision = Decision.ACCEPTED,
) -> ReviewSession:
    """Утверждает ревью двух рассмотренных замечаний VLM."""
    session = opened()

    session = decide(
        session,
        "vlm:1",
        first,
    )

    session = decide(
        session,
        "vlm:2",
        Decision.ACCEPTED,
    )

    return session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )


def confirmed(
    finding_id: str = "vlm:1",
    **changes: object,
) -> ConfirmedFindingArea:
    """Создаёт подтверждение области проверяющим."""
    params = {
        "job_id": JOB,
        "finding_id": finding_id,
        "page_number": 2,
        "regions": (REGION,),
        "confirmed_by": "engineer:2",
        "confirmed_at": NOW + timedelta(minutes=1),
        **changes,
    }

    return ConfirmedFindingArea(**params)


def test_accepted_vlm_without_verified_area_is_not_experience() -> None:
    """Утверждение ревью не подтверждает автоматически выбранную область."""
    session = sealed()

    assert session.findings[0].issue_box is None

    assert (
        select_experience_candidates(
            session=session,
            confirmed_areas=(),
        )
        == ()
    )

    assert (
        len(
            session.accepted_for_pdf().findings,
        )
        == 2
    )


def test_accepted_and_rejected_located_vlm_have_independent_labels() -> None:
    """Wise и Bad имеют общие требования к геометрии и отдельные метки."""
    session = sealed(
        Decision.REJECTED,
    )

    candidates = select_experience_candidates(
        session=session,
        confirmed_areas=(
            confirmed(),
            confirmed("vlm:2"),
        ),
    )

    assert [(item.tag, item.learning_use) for item in candidates] == [
        ("bad", LearningUse.NEGATIVE),
        ("wise", LearningUse.POSITIVE),
    ]

    assert all(item.issue_regions == (REGION,) for item in candidates)


@pytest.mark.parametrize(
    ("decision", "learning_use"),
    [
        (Decision.ACCEPTED, LearningUse.POSITIVE),
        (Decision.REJECTED, LearningUse.NEEDS_ADJUDICATION),
    ],
)
def test_edited_vlm_preserves_original_and_review_decision(
    decision: Decision,
    learning_use: LearningUse,
) -> None:
    """Исправленный отказ сохраняется и требует отдельной оценки для обучения."""
    session = opened().edit(
        finding_id="vlm:1",
        text="Исправленный пользователем текст",
        normative_basis="СП 9",
        actor="engineer:1",
        at=NOW,
        expected_revision=0,
    )

    session = decide(
        session,
        "vlm:1",
        decision,
    )

    session = decide(
        session,
        "vlm:2",
        Decision.ACCEPTED,
    )

    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )

    (example,) = select_experience_candidates(
        session=session,
        confirmed_areas=(confirmed(),),
    )

    assert example.tag == "edited"
    assert example.decision is decision
    assert example.learning_use is learning_use
    assert example.original_text == "Первый текст VLM"
    assert example.original_basis == "СП 1"
    assert example.text == "Исправленный пользователем текст"
    assert example.normative_basis == "СП 9"


def test_manual_accepted_gold_has_both_rectangles_and_source_provenance() -> None:
    """Gold использует выбранную пользователем область и сохраняет геометрию карточки."""
    session = opened().add_manual(
        finding_id=f"manual:{UUID(int=77)}",
        page_number=2,
        text="VLM не заметила обозначение",
        normative_basis="СП 4",
        issue_box=REGION,
        callout_box=CALLOUT,
        actor="engineer:3",
        at=NOW,
        expected_revision=0,
    )

    gold_id = session.findings[-1].finding_id

    for finding_id, choice in (
        ("vlm:1", Decision.REJECTED),
        ("vlm:2", Decision.ACCEPTED),
        (gold_id, Decision.ACCEPTED),
    ):
        session = decide(
            session,
            finding_id,
            choice,
        )

    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )

    candidates = select_experience_candidates(session=session, confirmed_areas=())
    example = next(item for item in candidates if item.tag == "gold")

    assert example.tag == "gold"
    assert example.issue_regions == (REGION,)
    assert example.callout_box == CALLOUT
    assert example.confirmed_by == "engineer:1"
    assert example.document_id == DOC
    assert example.source_sha256 == "f" * 64


def test_rejected_manual_remains_in_audit_not_training_candidates() -> None:
    """Отклонённое ручное замечание не становится положительным примером Gold."""
    session = opened().add_manual(
        finding_id=f"manual:{UUID(int=88)}",
        page_number=2,
        text="Ошибочное пользовательское замечание",
        normative_basis="",
        issue_box=REGION,
        callout_box=CALLOUT,
        actor="engineer:3",
        at=NOW,
        expected_revision=0,
    )

    for item in session.findings:
        session = decide(
            session,
            item.finding_id,
            Decision.REJECTED,
        )

    session = session.approve(
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision,
    )

    candidates = select_experience_candidates(session=session, confirmed_areas=())
    assert all(item.origin.value != "manual" for item in candidates)

    assert session.findings[-1].experience_tag == "gold"


def test_foreign_stale_or_duplicate_confirmations_fail_closed() -> None:
    """Подтверждения чужого задания, неверной страницы и дубликаты отклоняются."""
    session = sealed()

    for suspect in (
        confirmed(job_id=UUID(int=55)),
        confirmed(page_number=3),
        confirmed(
            confirmed_at=NOW - timedelta(seconds=1),
        ),
        confirmed("unknown-id"),
    ):
        with pytest.raises(ReviewError):
            select_experience_candidates(
                session=session,
                confirmed_areas=(suspect,),
            )

    with pytest.raises(ReviewError):
        select_experience_candidates(
            session=session,
            confirmed_areas=(
                confirmed(),
                confirmed(),
            ),
        )


def test_example_key_is_stable_but_changes_after_region_correction() -> None:
    """Правка областей требует обновления сохранённых изображений."""
    session = sealed()

    first = select_experience_candidates(
        session=session,
        confirmed_areas=(confirmed(),),
    )[0]

    repeated = select_experience_candidates(
        session=session,
        confirmed_areas=(confirmed(),),
    )[0]

    corrected = select_experience_candidates(
        session=session,
        confirmed_areas=(
            confirmed(
                regions=(
                    Rectangle(
                        300,
                        150,
                        430,
                        300,
                    ),
                ),
            ),
        ),
    )[0]

    assert first.example_key == repeated.example_key
    assert first.example_key != corrected.example_key


def test_confirmation_requires_actual_geometry_and_auditable_actor() -> None:
    """Нелокализованное замечание и искусственный флаг не подтверждают область."""
    for invalid in (
        {"regions": ()},
        {"confirmed_by": " "},
        {"confirmed_at": datetime(2026, 9, 25)},
        {"regions": ("not-a-rectangle",)},
    ):
        with pytest.raises(ReviewError):
            confirmed(**invalid)


def test_unapproved_review_cannot_produce_experience() -> None:
    """Отбирается только завершённая и явно утверждённая редакция."""
    with pytest.raises(ReviewNotReadyError):
        select_experience_candidates(
            session=opened(),
            confirmed_areas=(),
        )


class FakeReviews:
    """Минимальный порт чтения ревью для запроса отбора."""

    def __init__(
        self,
        session: ReviewSession | None,
    ) -> None:
        """Хранит один неизменяемый сеанс."""
        self.session = session

    async def load(
        self,
        job_id: UUID,
    ) -> ReviewSession | None:
        """Возвращает только совпадающее задание."""
        if self.session and self.session.job_id == job_id:
            return self.session

        return None


class FakeAreas:
    """Тестовый серверный порт подтверждений областей."""

    def __init__(self) -> None:
        """Учитывает обращения к серверному порту."""
        self.calls = 0

    async def load_confirmed(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ConfirmedFindingArea, ...]:
        """Возвращает одну подтверждённую область запрошенного задания."""
        self.calls += 1

        assert job_id == JOB

        return (confirmed(),)


@pytest.mark.asyncio
async def test_application_port_selects_without_writing_review() -> None:
    """Отбор читает утверждённое состояние и доверенные области без изменения БД."""
    areas = FakeAreas()

    selected = await SelectExperience(
        reviews=FakeReviews(sealed()),  # type: ignore[arg-type]
        areas=areas,
    ).execute(
        job_id=JOB,
    )

    assert len(selected) == 1
    assert selected[0].tag == "wise"
    assert areas.calls == 1


@pytest.mark.asyncio
async def test_application_does_not_fetch_areas_before_approval_or_unknown_job() -> (
    None
):
    """Не читает подтверждения неизвестного или незавершённого ревью."""
    areas = FakeAreas()

    with pytest.raises(ReviewNotReadyError):
        await SelectExperience(
            reviews=FakeReviews(opened()),  # type: ignore[arg-type]
            areas=areas,
        ).execute(
            job_id=JOB,
        )

    with pytest.raises(LookupError):
        await SelectExperience(
            reviews=FakeReviews(None),  # type: ignore[arg-type]
            areas=areas,
        ).execute(
            job_id=JOB,
        )

    assert areas.calls == 0
