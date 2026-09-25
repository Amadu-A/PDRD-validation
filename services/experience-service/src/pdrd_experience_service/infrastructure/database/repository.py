# services/experience-service/src/pdrd_experience_service/infrastructure/database/repository.py

"""PostgreSQL-репозиторий операционного Human Review.

Назначение файла:
- сохранять текущую редакцию отчёта и полную историю действий;
- восстанавливать доменные объекты при чтении из PostgreSQL;
- обеспечивать атомарное сохранение снимка и событий аудита;
- предотвращать потерю изменений при одновременной работе пользователей.

Важные гарантии:
1. При создании отчёта сначала записывается review_sessions,
   затем связанное событие review_events.
2. Оба INSERT выполняются в одной транзакции.
3. Изменение отчёта допускается только при совпадении revision.
4. Ошибки внешнего ключа не выдаются за дублирование отчёта.

Репозиторий реализует существующий application port ReviewRepository.
Доменная модель ничего не знает о SQLAlchemy и PostgreSQL.
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_experience_service.domain.review import (
    ReviewConflictError,
    ReviewError,
    ReviewEvent,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.codec import (
    event_from_json,
    event_to_json,
    snapshot_from_json,
    snapshot_to_json,
)
from pdrd_experience_service.infrastructure.database.models import (
    ReviewEventModel,
    ReviewSessionModel,
)


class SqlAlchemyReviewRepository:
    """Хранилище Human Review с транзакциями и контролем ревизий."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
    ) -> None:
        """Принимает фабрику сессий через Dependency Injection.

        Каждый вызов репозитория создаёт собственную AsyncSession.
        Это позволяет независимо выполнять конкурентные транзакции.
        """
        self._session_factory = session_factory

    async def load(
        self,
        job_id: UUID,
    ) -> ReviewSession | None:
        """Загружает текущую редакцию и всю историю действий.

        Дополнительно проверяет последовательность ревизий.
        Если события аудита отсутствуют или нарушен их порядок,
        повреждённый отчёт не возвращается вызывающему коду.
        """
        async with self._session_factory() as database:
            row = await database.get(
                ReviewSessionModel,
                job_id,
            )

            if row is None:
                return None

            results = await database.scalars(
                select(ReviewEventModel)
                .where(
                    ReviewEventModel.job_id == job_id,
                )
                .order_by(
                    ReviewEventModel.session_revision,
                )
            )

            history = tuple(
                event_from_json(
                    action=event.action,
                    actor=event.actor,
                    occurred_at=event.occurred_at,
                    revision=event.session_revision,
                    details=event.details,
                )
                for event in results.all()
            )

            revisions = tuple(event.session_revision for event in history)

            expected_revisions = tuple(range(row.revision + 1))

            if revisions != expected_revisions:
                raise ReviewError(
                    "История Human Review неполная "
                    "или содержит нарушения последовательности ревизий."
                )

            snapshot = snapshot_from_json(
                row.snapshot,
                history,
            )

            if (
                snapshot.job_id != job_id
                or snapshot.revision != row.revision
                or snapshot.approved_revision != row.approved_revision
            ):
                raise ReviewError(
                    "Сохранённый Human Review не соответствует "
                    "метаданным текущей редакции."
                )

            return snapshot

    async def insert(
        self,
        session: ReviewSession,
    ) -> None:
        """Атомарно создаёт отчёт и первое событие аудита.

        PostgreSQL требует существования родительской записи
        review_sessions до вставки дочерней записи review_events.

        SQLAlchemy без ORM relationship не обязан выполнять INSERT
        в порядке вызовов database.add(). Поэтому родительская
        запись явно отправляется в PostgreSQL через flush().

        flush() НЕ завершает транзакцию. Если вставка события
        завершится ошибкой, родительская запись тоже откатится.
        """
        if session.revision != 0 or len(session.history) != 1:
            raise ReviewError(
                "Первоначальный Human Review должен содержать "
                "ровно одно событие открытия и ревизию 0."
            )

        event = session.history[0]

        if event.session_revision != 0 or event.action.value != "opened":
            raise ReviewError(
                "История нового отчёта должна начинаться с события opened и ревизии 0."
            )

        try:
            async with (
                self._session_factory() as database,
                database.begin(),
            ):
                review_row = ReviewSessionModel(
                    job_id=session.job_id,
                    document_id=session.document_id,
                    source_sha256=session.source_sha256,
                    revision=session.revision,
                    approved_revision=session.approved_revision,
                    snapshot=snapshot_to_json(session),
                    updated_at=session.opened_at,
                )

                database.add(
                    review_row,
                )

                # ВАЖНО:
                # Отправляем родительскую запись в PostgreSQL
                # до создания дочернего события аудита.
                #
                # При этом остаёмся внутри общей транзакции.
                await database.flush()

                database.add(
                    self._event_row(
                        session.job_id,
                        event,
                    )
                )

        except IntegrityError as error:
            if self._is_unique_violation(error):
                raise ReviewConflictError(
                    "Human Review для этого задания уже существует."
                ) from error

            # Ошибки FK, CHECK и прочие нарушения целостности
            # не должны скрываться под сообщением о дубликате.
            raise

    async def update(
        self,
        session: ReviewSession,
        *,
        expected_revision: int,
    ) -> None:
        """Сохраняет одну новую ревизию с проверкой конкуренции.

        PostgreSQL выполняет UPDATE только при совпадении
        фактической ревизии и expected_revision.

        Если два пользователя изменяют один отчёт одновременно,
        успешным может быть только один конкурентный UPDATE.

        Событие аудита добавляется в той же транзакции.
        """
        new_events = tuple(
            event
            for event in session.history
            if event.session_revision > expected_revision
        )

        if (
            session.revision != expected_revision + 1
            or len(new_events) != 1
            or new_events[0].session_revision != session.revision
        ):
            raise ReviewError(
                "Обновление Human Review должно содержать "
                "ровно одну новую ревизию и одно событие аудита."
            )

        event = new_events[0]

        statement = (
            update(ReviewSessionModel)
            .where(
                ReviewSessionModel.job_id == session.job_id,
                ReviewSessionModel.revision == expected_revision,
            )
            .values(
                revision=session.revision,
                approved_revision=session.approved_revision,
                snapshot=snapshot_to_json(session),
                updated_at=event.occurred_at,
            )
            .returning(
                ReviewSessionModel.job_id,
            )
        )

        try:
            async with (
                self._session_factory() as database,
                database.begin(),
            ):
                changed = await database.scalar(
                    statement,
                )

                if changed is None:
                    raise ReviewConflictError(
                        "Human Review был изменён другим пользователем "
                        "или указанная ревизия отсутствует."
                    )

                database.add(
                    self._event_row(
                        session.job_id,
                        event,
                    )
                )

        except IntegrityError as error:
            if self._is_unique_violation(error):
                raise ReviewConflictError(
                    "Конкурентная транзакция уже сохранила эту ревизию Human Review."
                ) from error

            raise

    @staticmethod
    def _is_unique_violation(
        error: IntegrityError,
    ) -> bool:
        """Отличает повторную запись от других ошибок PostgreSQL.

        Код SQLSTATE 23505 означает нарушение ограничения UNIQUE
        либо PRIMARY KEY.

        SQLSTATE 23503 означает нарушение внешнего ключа.
        Подменять такую ошибку ReviewConflictError нельзя:
        она указывает на другую неисправность хранения.
        """
        original = error.orig

        return (
            getattr(original, "sqlstate", None) == "23505"
            or getattr(original, "pgcode", None) == "23505"
        )

    @staticmethod
    def _event_row(
        job_id: UUID,
        event: ReviewEvent,
    ) -> ReviewEventModel:
        """Создаёт ORM-запись события, не изменяя доменный объект."""
        return ReviewEventModel(
            job_id=job_id,
            session_revision=event.session_revision,
            action=event.action.value,
            actor=event.actor,
            occurred_at=event.occurred_at,
            details=event_to_json(event),
        )
