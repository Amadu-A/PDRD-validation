# services/experience-service/src/pdrd_experience_service/infrastructure/database/confirmed_areas.py

"""PostgreSQL-адаптер подтверждения областей с CAS и неизменяемым аудитом.

Транзакция блокирует строку Review: редактирование текста, изменение решения
и подтверждение координат не могут пройти через одну устаревшую ревизию.
При отборе подпись сверяется с текущим источником и последней правкой текста.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_experience_service.application.ports.review import ReviewRepository
from pdrd_experience_service.domain.area_confirmation import (
    AreaConfirmation,
    AreaConfirmationReceipt,
    build_confirmation,
    content_signature,
    find_vlm_finding,
)
from pdrd_experience_service.domain.experience_selection import (
    ConfirmedFindingArea,
)
from pdrd_experience_service.domain.review import (
    Rectangle,
    ReviewConflictError,
    ReviewError,
    ReviewSession,
)
from pdrd_experience_service.domain.review_export import AreaStatus
from pdrd_experience_service.infrastructure.database.codec import (
    event_from_json,
    snapshot_from_json,
)
from pdrd_experience_service.infrastructure.database.models import (
    AreaConfirmationEventModel,
    ConfirmedAreaModel,
    ReviewEventModel,
    ReviewSessionModel,
)


def _boxes(
    regions: tuple[Rectangle, ...],
) -> list[dict[str, float]]:
    """Сохраняет нормализованные области как простой JSONB без потери точности."""
    return [
        {
            "x_min": float(box.x_min),
            "y_min": float(box.y_min),
            "x_max": float(box.x_max),
            "y_max": float(box.y_max),
        }
        for box in regions
    ]


def _area_snapshot(
    row: ConfirmedAreaModel,
) -> dict[str, object]:
    """Фиксирует неизменяемый снимок геометрии в каждом событии аудита."""
    return {
        "revision": row.revision,
        "page_number": row.page_number,
        "source_sha256": row.source_sha256,
        "content_signature": row.content_signature,
        "review_revision": row.review_revision,
        "regions": row.regions,
        "mode": row.mode,
        "note": row.note,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": row.confirmed_at.isoformat(),
        "active": row.active,
    }


class SqlAlchemyConfirmedAreasRepository:
    """Записывает инженерные подтверждения отдельно от исходных VLM-предложений."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        reviews: ReviewRepository,
    ) -> None:
        """Внедряет фабрику транзакций и текущий источник Review."""
        self._sessions = session_factory
        self._reviews = reviews

    @staticmethod
    async def _locked_review(
        database: AsyncSession,
        job_id: UUID,
        expected_revision: int,
    ) -> ReviewSession:
        """Блокирует Review и проверяет, что команда не пришла из старой вкладки."""
        row = await database.get(
            ReviewSessionModel,
            job_id,
            with_for_update=True,
        )

        if row is None or row.revision != expected_revision:
            raise ReviewConflictError("Human Review уже изменён или отсутствует.")

        audit_rows = (
            await database.scalars(
                select(ReviewEventModel)
                .where(ReviewEventModel.job_id == job_id)
                .order_by(ReviewEventModel.session_revision)
            )
        ).all()

        if [event.session_revision for event in audit_rows] != list(
            range(row.revision + 1)
        ):
            raise ReviewError("История Human Review неполная.")

        history = tuple(
            event_from_json(
                action=event.action,
                actor=event.actor,
                occurred_at=event.occurred_at,
                revision=event.session_revision,
                details=event.details,
            )
            for event in audit_rows
        )

        review = snapshot_from_json(
            row.snapshot,
            history,
        )

        if (
            review.job_id != job_id
            or review.revision != row.revision
            or review.source_sha256 != row.source_sha256
            or review.approved_revision != row.approved_revision
        ):
            raise ReviewError("Снимок Review не соответствует данным PostgreSQL.")

        return review

    @staticmethod
    def _audit_row(
        *,
        row: ConfirmedAreaModel,
        action: str,
        actor: str,
        at: datetime,
        before: dict[str, object] | None,
        reason: str,
    ) -> AreaConfirmationEventModel:
        """Добавляет новое событие, не переписывая предыдущую историю."""
        return AreaConfirmationEventModel(
            job_id=row.job_id,
            finding_id=row.finding_id,
            revision=row.revision,
            review_revision=row.review_revision,
            action=action,
            actor=actor,
            occurred_at=at,
            details={
                "reason": reason,
                "before": before,
                "after": _area_snapshot(row),
            },
        )

    async def save(
        self,
        *,
        confirmation: AreaConfirmation,
        expected_confirmation_revision: int,
    ) -> AreaConfirmationReceipt:
        """Сохраняет область и аудит в одной транзакции с двумя проверками версий."""
        if expected_confirmation_revision < 0:
            raise ReviewError("Ревизия подтверждения не может быть отрицательной.")

        try:
            async with (
                self._sessions() as database,
                database.begin(),
            ):
                review = await self._locked_review(
                    database,
                    confirmation.job_id,
                    confirmation.review_revision,
                )

                checked = build_confirmation(
                    session=review,
                    finding_id=confirmation.finding_id,
                    regions=confirmation.regions,
                    mode=confirmation.mode,
                    note=confirmation.note,
                    actor=confirmation.confirmed_by,
                    at=confirmation.confirmed_at,
                    expected_review_revision=confirmation.review_revision,
                )

                if checked != confirmation:
                    raise ReviewConflictError(
                        "Подтверждение не соответствует текущему источнику "
                        "или редакции."
                    )

                result = await self.write_confirmation(
                    database,
                    confirmation=confirmation,
                    expected_confirmation_revision=expected_confirmation_revision,
                )

        except IntegrityError as error:
            if (
                getattr(error.orig, "sqlstate", None) == "23505"
                or getattr(error.orig, "pgcode", None) == "23505"
            ):
                raise ReviewConflictError(
                    "Параллельное подтверждение области."
                ) from error
            raise

        return result

    async def write_confirmation(
        self,
        database: AsyncSession,
        *,
        confirmation: AreaConfirmation,
        expected_confirmation_revision: int | None = None,
        keep_current: bool = False,
    ) -> AreaConfirmationReceipt:
        """Пишет проверенную область в уже открытой транзакции с блокировкой Review.

        Внешний save проверяет отдельный CAS. Совместное решение использует
        блокировку Review и сохраняет актуальное подтверждение без нового события.
        Вызов не открывает и не фиксирует самостоятельную транзакцию.
        """
        row = await database.get(
            ConfirmedAreaModel,
            (confirmation.job_id, confirmation.finding_id),
            with_for_update=True,
        )
        current_revision = 0 if row is None else row.revision
        if (
            expected_confirmation_revision is not None
            and current_revision != expected_confirmation_revision
        ):
            raise ReviewConflictError("Подтверждение области уже изменено.")
        boxes = _boxes(confirmation.regions)
        if (
            keep_current
            and row is not None
            and row.active
            and row.content_signature == confirmation.content_signature
            and row.source_sha256 == confirmation.source_sha256
            and row.page_number == confirmation.page_number
            and row.regions == boxes
        ):
            return AreaConfirmationReceipt(
                row.job_id, row.finding_id, row.revision, True
            )
        before = None if row is None else _area_snapshot(row)
        action = "confirmed" if row is None else "corrected"
        if row is None:
            row = ConfirmedAreaModel(
                job_id=confirmation.job_id, finding_id=confirmation.finding_id
            )
            database.add(row)
        row.revision = current_revision + 1
        row.page_number = confirmation.page_number
        row.source_sha256 = confirmation.source_sha256
        row.content_signature = confirmation.content_signature
        row.review_revision = confirmation.review_revision
        row.regions = boxes
        row.mode = confirmation.mode.value
        row.note = confirmation.note
        row.confirmed_by = confirmation.confirmed_by
        row.confirmed_at = confirmation.confirmed_at
        row.active = True
        # FK события требует строку области; flush не завершает общую транзакцию.
        await database.flush()
        database.add(
            self._audit_row(
                row=row,
                action=action,
                actor=confirmation.confirmed_by,
                at=confirmation.confirmed_at,
                before=before,
                reason=confirmation.note,
            )
        )
        return AreaConfirmationReceipt(row.job_id, row.finding_id, row.revision, True)

    async def revoke(
        self,
        *,
        job_id: UUID,
        finding_id: str,
        actor: str,
        reason: str,
        at: datetime,
        expected_review_revision: int,
        expected_confirmation_revision: int,
    ) -> AreaConfirmationReceipt:
        """Отзывает подтверждение; старые координаты остаются в журнале событий."""
        if (
            not isinstance(actor, str)
            or not 1 <= len(actor.strip()) <= 128
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 1000
            or not isinstance(at, datetime)
            or at.tzinfo is None
            or at.utcoffset() is None
        ):
            raise ReviewError(
                "Отзыв требует инженера, причины и времени с часовым поясом."
            )

        async with (
            self._sessions() as database,
            database.begin(),
        ):
            review = await self._locked_review(
                database,
                job_id,
                expected_review_revision,
            )

            find_vlm_finding(
                review,
                finding_id,
            )

            row = await database.get(
                ConfirmedAreaModel,
                (job_id, finding_id),
                with_for_update=True,
            )

            if (
                row is None
                or not row.active
                or row.revision != expected_confirmation_revision
            ):
                raise ReviewConflictError("Подтверждение уже изменено либо отозвано.")

            before = _area_snapshot(row)

            row.revision += 1
            row.review_revision = expected_review_revision
            row.active = False

            timestamp = at.astimezone(UTC)

            await database.flush()

            database.add(
                self._audit_row(
                    row=row,
                    action="revoked",
                    actor=actor.strip(),
                    at=timestamp,
                    before=before,
                    reason=reason.strip(),
                )
            )

            return AreaConfirmationReceipt(
                job_id=job_id,
                finding_id=finding_id,
                confirmation_revision=row.revision,
                active=False,
            )

    async def load_status(self, *, review: ReviewSession) -> tuple[AreaStatus, ...]:
        """Возвращает CAS-версии всех подтверждений и актуальность для этого Review."""
        async with self._sessions() as database:
            rows = (
                await database.scalars(
                    select(ConfirmedAreaModel)
                    .where(
                        ConfirmedAreaModel.job_id == review.job_id,
                    )
                    .order_by(ConfirmedAreaModel.finding_id)
                )
            ).all()
        findings = {item.finding_id: item for item in review.findings}
        statuses = []
        for row in rows:
            finding = findings.get(row.finding_id)
            valid = bool(
                row.active
                and finding is not None
                and finding.origin.value == "vlm"
                and row.source_sha256 == review.source_sha256
                and row.page_number == finding.page_number
                and row.page_number in review.allowed_pages
                and row.confirmed_at >= review.opened_at
                and row.content_signature == content_signature(review, finding)
            )
            statuses.append(
                AreaStatus(
                    finding_id=row.finding_id,
                    revision=row.revision,
                    valid=valid,
                    regions=tuple(Rectangle(**box) for box in row.regions)
                    if valid
                    else (),
                )
            )
        return tuple(statuses)

    async def load_confirmed(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ConfirmedFindingArea, ...]:
        """Отбирает только активные области с актуальной привязкой к Review.

        История и старые рамки не удаляются, но не возвращаются для обучения.
        Перед реальной индексацией потребуется повторная транзакционная
        проверка актуальности утверждённой версии Review.
        """
        review = await self._reviews.load(job_id)
        if review is None:
            return ()

        review.accepted_for_pdf()

        findings = {item.finding_id: item for item in review.findings}

        async with self._sessions() as database:
            rows = (
                await database.scalars(
                    select(ConfirmedAreaModel).where(
                        ConfirmedAreaModel.job_id == job_id,
                        ConfirmedAreaModel.active.is_(True),
                    )
                )
            ).all()

        result: list[ConfirmedFindingArea] = []

        for row in rows:
            finding = findings.get(row.finding_id)

            if (
                finding is None
                or not row.regions
                or row.source_sha256 != review.source_sha256
                or row.page_number != finding.page_number
                or row.confirmed_at < review.opened_at
                or row.content_signature != content_signature(review, finding)
            ):
                continue

            find_vlm_finding(
                review,
                row.finding_id,
            )

            result.append(
                ConfirmedFindingArea(
                    job_id=row.job_id,
                    finding_id=row.finding_id,
                    page_number=row.page_number,
                    regions=tuple(Rectangle(**box) for box in row.regions),
                    confirmed_by=row.confirmed_by,
                    confirmed_at=row.confirmed_at,
                )
            )

        return tuple(result)
